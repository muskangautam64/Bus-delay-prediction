# Bus Delay Prediction Service
A end-to-end(ish) ML pipeline for a simple bus delay prediction service, which can be deployed in a Kubernetes cluster.


## Motivation

The core idea of the project uses ML to provide an estimate of the delay of a New York City bus (NYC), given features such as (a) the bus line / direction of the bus; (b) a station where to catch the bus; (c) the time of the day; (d) day of the week.

The typical usage of the system is:

1. A bus user issues a request towards a 'always-on' bus delay prediction service, requesting a delay estimate for bus line X at stop Y
2. The bus delay prediction service replies with a current estimate of a delay, in minutes
3. The user decides to adjust his/her schedule according to the estimate provided by the service

## Dataset

This project uses the [NYC Bus Data](https://www.kaggle.com/datasets/stoney71/new-york-city-transport-statistics) dataset from Kaggle, with Kaggle ID `stoney71/new-york-city-transport-statistics`.
