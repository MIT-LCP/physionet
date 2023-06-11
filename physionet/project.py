"""
The dataset module contains code for loading and processing PhysioNet data.
"""
import requests


def hello():
    print("Hello world!")


def _get_request(root='https://physionet.org/api/v1/',
                 endpoint='project/published/'):
    """
    Make a GET request to the PhysioNet API.

    Returns:
        response (requests.models.Response): Response object from the API call.
    """
    url = root + endpoint
    response = requests.get(url)

    if not response.status_code == 200:
        raise Exception(f'Error: {response.status_code}')

    return response
