import os
import argparse
import numpy as np
import xgboost as xgb
import mlflow

from pathlib import Path
from hyperopt import fmin, tpe, hp, STATUS_OK, Trials
from hyperopt.pyll import scope
from sklearn.ensemble import RandomForestRegressor
from sklearn.metrics import mean_squared_error

from src.utils import load_pickle


def train_random_forest_regressor(
    X_train:np.ndarray, 
    y_train:np.ndarray, 
    X_val:np.ndarray, 
    y_val:np.ndarray
):
    # enable mlflow sklearn autologging
    mlflow.sklearn.autolog()

    def objective(params):
        with mlflow.start_run():
            mlflow.set_tag('model', 'random-forest-regressor')

            rf = RandomForestRegressor(**params)
            rf.fit(X_train, y_train)
            y_pred = rf.predict(X_val)            
            rmse = mean_squared_error(y_val, y_pred, squared=False)
            
            mlflow.log_metric('rmse', rmse)

        return {'loss': rmse, 'status': STATUS_OK}

    search_space = {
        'max_depth': scope.int(hp.quniform('max_depth', 1, 20, 1)),
        'n_estimators': scope.int(hp.quniform('n_estimators', 10, 50, 1)),
        'min_samples_split': scope.int(hp.quniform('min_samples_split', 2, 10, 1)),
        'min_samples_leaf': scope.int(hp.quniform('min_samples_leaf', 1, 4, 1)),
        'random_state': 42
    }

    rstate = np.random.default_rng(42)  # for reproducible results
    fmin(
        fn=objective,
        space=search_space,
        algo=tpe.suggest,
        max_evals=10,
        trials=Trials(),
        rstate=rstate
    )


def train_xgboost(
    X_train:np.ndarray, 
    y_train:np.ndarray, 
    X_val:np.ndarray, 
    y_val:np.ndarray
):
    # enable mlflow xgboost autologging
    mlflow.xgboost.autolog()

    # xgboost requires a conversion of input types
    train_data = xgb.DMatrix(X_train, label=y_train)
    validation_data = xgb.DMatrix(X_val, label=y_val)

    # objective function to be used by hyperopt
    def objective(params):
        with mlflow.start_run():
            mlflow.set_tag('model', 'xgboost-regressor')

            booster = xgb.train(
                params=params,
                dtrain=train_data,
                num_boost_round=100,
                evals=[(validation_data, 'validation')],
                early_stopping_rounds=10
            )
            y_pred = booster.predict(validation_data)
            rmse = mean_squared_error(y_val, y_pred, squared=False)

            mlflow.log_metric('rmse', rmse)

        return {'loss': rmse, 'status': STATUS_OK}

    # hyperparameter search space for xgboost model
    search_space = {
        'max_depth': scope.int(hp.quniform('max_depth', 4, 100, 1)),
        'learning_rate': hp.loguniform('learning_rate', -3, 0),
        'reg_alpha': hp.loguniform('reg_alpha', -5, -1),
        'reg_lambda': hp.loguniform('reg_lambda', -6, -1),
        'min_child_weight': hp.loguniform('min_child_weight', -1, 3),
        'objective': 'reg:squarederror',
        'seed': 42
    }

    # run hyperopt optimization
    best_result = fmin(
        fn=objective,
        space=search_space,
        algo=tpe.suggest,
        max_evals=10,
        trials=Trials()
    )


def train(
    input_dir: str, 
    output_dir: str,
    mlflow_tracking_uri: str,
    mlflow_experiment: str
):
    # initialize mlflow
    mlflow.set_tracking_uri(mlflow_tracking_uri)
    mlflow.set_experiment(mlflow_experiment)

    print(f'mlflow tracking uri : {mlflow.get_tracking_uri()}')
    print(f'mlflow experiments : {mlflow.list_experiments()}')

    # load dataset splits
    input_dir = Path(input_dir)
    X_train, y_train = load_pickle(input_dir / "train.pkl")
    X_val, y_val = load_pickle(input_dir / "validation.pkl")

    # train and evaluate xgboost model
    train_xgboost(X_train, y_train, X_val, y_val)

    # train and evaluate random forest regressor model
    train_random_forest_regressor(X_train, y_train, X_val, y_val)


if __name__ == '__main__':
    parser = argparse.ArgumentParser()

    parser.add_argument(
        "--input_dir",
        help="location of 'featurized' NYC bus dataset train, validation and test splits",
        required=True
    )

    parser.add_argument(
        "--output_dir",
        help="location where models will be saved",
        required=False
    )

    args = parser.parse_args()

    # expand script arguments with mlflow parameters
    train_args = vars(args) | {
        'mlflow_tracking_uri' : os.getenv('MLFLOW_TRACKING_URI', 'http://127.0.0.1:5000'),
        'mlflow_experiment' : os.getenv('MLFLOW_EXPERIMENT_NAME', 'nyc-bus-delay-predictor')
    }

    train(**train_args)