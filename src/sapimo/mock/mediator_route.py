from typing import Callable
import logging
from enum import Enum

from fastapi import Request, Response
from fastapi.routing import APIRoute
from sapimo.utils import setup_logger

logger = setup_logger(__file__)


class ReturnMode(Enum):
    Default = 0
    Lambda = 1
    Mock = 2
    Example = 3


class MediatorRoute(APIRoute):
    """
        custom APIRoute
            - generate lambda event from request
            - switch the return value depending on the mode
            - s3 and dynamo sync (moto <-> local dir)
    """

    return_mode = ReturnMode.Default
    return_code = 200

    def get_route_handler(self) -> Callable:
        original_route_handler = super().get_route_handler()

        async def custom_handler(req: Request) -> Response:

            response: Response = await original_route_handler(req)
            body = response.body.decode("utf-8")

            return_val = self.return_mode
            if self.return_mode == ReturnMode.Default:
                if not body:
                    logger.info("return mock")
                    return_val = ReturnMode.Mock
                else:
                    logger.info("lambda execute")
                    return_val = ReturnMode.Lambda

            if return_val == ReturnMode.Lambda:
                res = await self.lambda_manager.run_by_api(req)
                self.data_manager.sync()
                updated, deleted = self.data_manager.get_change("s3")
                while updated or deleted:
                    await self.lambda_manager.run_by_trigger(updated, deleted)
                    self.data_manager.sync()
                    updated, deleted = self.data_manager.get_change("s3")

            elif return_val == ReturnMode.Mock:
                res = response
            elif return_val == ReturnMode.Example:
                res = await self.lambda_manager.example(req, status=self.return_code)
            return res
        return custom_handler


def set_mode(mode: ReturnMode, status=200):
    MediatorRoute.return_mode = mode
    MediatorRoute.return_code = status
