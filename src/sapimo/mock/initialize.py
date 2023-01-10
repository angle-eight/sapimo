"""
    actual "uvicorn run" entry point
    (imported from mock_api/app.py)
"""

from fastapi.middleware.cors import CORSMiddleware
from fastapi import FastAPI

from .executer.lambda_invoker import LambdaInvoker
from .mediator_route import MediatorRoute
from .mock_manager import MockManager
from sapimo.constants import CONFIG_FILE
from sapimo.utils import setup_logger
logger = setup_logger(__file__)

if not CONFIG_FILE.exists():
    print("config file not found")
    exit(0)


mock = MockManager(config_file=CONFIG_FILE)


def on_start():
    """ start mock and setup aws resources from local dir"""
    mock.start()
    logger.info("mock start")
    mock.init_data()


def on_stop():
    """ stop mock and sync local"""
    mock.sync()
    mock.stop()
    logger.info("mock stop")


api = FastAPI(on_startup=[on_start], on_shutdown=[on_stop])
api.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)
# fast api settings
MediatorRoute.lambda_manager = LambdaInvoker(CONFIG_FILE)
MediatorRoute.data_manager = mock
api.router.route_class = MediatorRoute
