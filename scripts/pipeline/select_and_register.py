import os
import sys
import argparse
from pathlib import Path

import numpy as np
import mlflow
import xgboost as xgb
from hyperopt import hp, space_eval
from hyperopt.pyll import scope
from mlflow.entities import ViewType
from mlflow.tracking import MlflowClient
from sklearn.metrics import mean_squared_error
from sklearn.ensemble import RandomForestRegressor

from src.utils import load_pickle


def load_dataset_splits(input_dir: str) -> tuple[np.ndarray]:
    input_dir = Path(input_dir)

    X_train, y_train = load_pickle(input_dir / "train.pkl")
    X_val, y_val = load_pickle(input_dir / "validation.pkl")
    X_test, y_test = load_pickle(input_dir / "test.pkl")

    return X_train, y_train, X_val, y_val, X_test, y_test


def train_and_log_xgboost(input_dir: str, params):
    # enable mlflow xgboost autologging
    mlflow.xgboost.autolog()

    # load dataset splits
    X_train, y_train, X_val, y_val, X_test, y_test = load_dataset_splits(input_dir)

    # xgboost requires a conversion of input types
    train_data = xgb.DMatrix(X_train, label=y_train)
    validation_data = xgb.DMatrix(X_val, label=y_val)
    test_data = xgb.DMatrix(X_test, label=y_test)

    # search space used for xgboost regressor models
    search_space = {
        'max_depth': scope.int(hp.quniform('max_depth', 4, 100, 1)),
        'learning_rate': hp.loguniform('learning_rate', -3, 0),
        'reg_alpha': hp.loguniform('reg_alpha', -5, -1),
        'reg_lambda': hp.loguniform('reg_lambda', -6, -1),
        'min_child_weight': hp.loguniform('min_child_weight', -1, 3),
        'objective': 'reg:squarederror',
        'seed': 42,
    }

    with mlflow.start_run():
        mlflow.set_tag('model', 'xgboost-regressor')
        params = space_eval(search_space, params)
        booster = xgb.train(
            params=params,
            dtrain=train_data,
            num_boost_round=100,
            evals=[(validation_data, 'validation')],
            early_stopping_rounds=10,
        )

        # log validation error
        val_rmse = mean_squared_error(
            y_val, booster.predict(validation_data), squared=False
        )
        mlflow.log_metric('validation_rmse', val_rmse)

        # log test error
        test_rmse = mean_squared_error(
            y_test, booster.predict(test_data), squared=False
        )
        mlflow.log_metric('test_rmse', test_rmse)


def train_and_log_train_random_forest_regressor(input_dir: str, params):
    # enable mlflow xgboost autologging
    mlflow.sklearn.autolog()

    # load dataset splits
    X_train, y_train, X_val, y_val, X_test, y_test = load_dataset_splits(input_dir)

    # search space used for sklearn regressor models
    search_space = {
        'max_depth': scope.int(hp.quniform('max_depth', 1, 20, 1)),
        'n_estimators': scope.int(hp.quniform('n_estimators', 10, 50, 1)),
        'min_samples_split': scope.int(hp.quniform('min_samples_split', 2, 10, 1)),
        'min_samples_leaf': scope.int(hp.quniform('min_samples_leaf', 1, 4, 1)),
        'random_state': 42,
    }

    with mlflow.start_run():
        mlflow.set_tag('model', 'random-forest-regressor')

        params = space_eval(search_space, params)
        rf = RandomForestRegressor(**params)
        rf.fit(X_train, y_train)

        # log validation error
        val_rmse = mean_squared_error(y_val, rf.predict(X_val), squared=False)
        mlflow.log_metric('validation_rmse', val_rmse)

        # log test error
        test_rmse = mean_squared_error(y_test, rf.predict(X_test), squared=False)
        mlflow.log_metric('test_rmse', test_rmse)


TRAIN_AND_LOG_FUNC = {
    'xgboost-regressor': train_and_log_xgboost,
    'random-forest-regressor': train_and_log_train_random_forest_regressor,
}


def register_model(
    run: mlflow.entities.Run, mlflow_tracking_uri: str, model_name: str
) -> bool:

    # initialize mlflow : tracking uri
    mlflow.set_tracking_uri(mlflow_tracking_uri)

    client = MlflowClient()

    # extract rmse of current production model
    try:
        production = client.get_latest_versions(model_name, ['Production'])
    except mlflow.exceptions.RestException:
        production = []

    if production:
        production_run = client.get_run(run_id=production[-1].run_id)
        production_rmse = production_run.data.metrics['test_rmse']

    # register and transition to 'Staging' if candidate model performs better than
    # current production model or if no production model exists
    if not production or run.data.metrics['test_rmse'] < production_rmse:
        # registration
        model_version = mlflow.register_model(
            model_uri=f"runs:/{run.info.run_id}/model",
            name=model_name,
            tags=run.data.tags,
        )
        # transition to 'Staging'
        client.transition_model_version_stage(
            name=model_name, version=model_version.version, stage='Staging'
        )

        return True

    return False


def select_model(
    input_dir: str,
    number_top_runs: int,
    mlflow_tracking_uri: str,
    mlflow_hpo_experiment: str,
    mlflow_select_experiment: str,
) -> mlflow.entities.Run:

    # initialize mlflow : tracking uri and experiment name
    mlflow.set_tracking_uri(mlflow_tracking_uri)
    mlflow.set_experiment(mlflow_select_experiment)

    client = MlflowClient()

    # extract hpo experiment
    hpo_experiment = client.get_experiment_by_name(mlflow_hpo_experiment)

    # search top n runs according to rmse
    runs = client.search_runs(
        experiment_ids=hpo_experiment.experiment_id,
        run_view_type=ViewType.ACTIVE_ONLY,
        max_results=number_top_runs,
        order_by=["metrics.rmse ASC"],
    )

    # train, validate and test using parameters of top runs
    for run in runs:
        TRAIN_AND_LOG_FUNC[run.data.tags['model']](input_dir, run.data.params)

    # select model with lowest test RMSE
    select_experiment = client.get_experiment_by_name(mlflow_select_experiment)
    best_run = client.search_runs(
        experiment_ids=select_experiment.experiment_id,
        run_view_type=ViewType.ACTIVE_ONLY,
        max_results=1,
        order_by=["metrics.test_rmse ASC"],
    )[0]

    return best_run


if __name__ == '__main__':
    parser = argparse.ArgumentParser()

    parser.add_argument(
        "--input_dir",
        help="location of 'featurized' NYC bus dataset train, validation and test splits",
        required=True,
    )

    parser.add_argument(
        "--number_top_runs",
        help="choose top n runs for final selection (default: 5)",
        default=5,
        required=False,
    )

    args = parser.parse_args()

    # expand script arguments with mlflow parameters
    mlflow_args = {
        'mlflow_tracking_uri': os.getenv(
            'MLFLOW_TRACKING_URI', 'http://127.0.0.1:5000'
        ),
        'mlflow_hpo_experiment': os.getenv(
            'MLFLOW_HPO_EXPERIMENT_NAME', 'nyc-bus-delay-predictor-hpo'
        ),
        'mlflow_select_experiment': os.getenv(
            'MLFLOW_SELECT_EXPERIMENT_NAME', 'nyc-bus-delay-predictor-select'
        ),
        'mlflow_model_name': os.getenv('MLFLOW_MODEL_NAME', 'nyc-bus-delay-predictor'),
    }

    # select best candidate model
    mlflow_run = select_model(
        args.input_dir,
        args.number_top_runs,
        mlflow_args['mlflow_tracking_uri'],
        mlflow_args['mlflow_hpo_experiment'],
        mlflow_args['mlflow_select_experiment'],
    )

    # register best candidate model model and transition to staging (if 'better' than current)
    rc = register_model(
        mlflow_run, mlflow_args['mlflow_tracking_uri'], mlflow_args['mlflow_model_name']
    )

    print(
        f"model {mlflow_args['mlflow_model_name']} {'updated' if rc else 'not updated'}"
    )

    sys.exit(0)
