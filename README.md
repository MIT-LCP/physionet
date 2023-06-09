# PhysioNet

A collection of tools for working with the [PhysioNet](http://physionet.org/) repository.

## Installation

```bash
pip install physionet
```

## Usage

```python
import physionet as pn

# Download a dataset
pn.download('ptbdb', 'data/ptbdb')

# List all datasets
pn.list_datasets()
```
