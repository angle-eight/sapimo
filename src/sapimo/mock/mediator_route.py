from typing import Callable
from logging import DEBUG
from enum import Enum
from datetime import datetime

from fastapi import Request, Response
from fastapi.responses import JSONResponse
from fastapi.routing import APIRoute
from sapimo.utils import LogManager

logger = LogManager.setup_logger(__file__, level=DEBUG)


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
                if not body or body == "null":
                    return_val = ReturnMode.Lambda
                else:
                    return_val = ReturnMode.Mock

            if return_val == ReturnMode.Lambda:
                logger.info(f"[{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}]==========")
                logger.info(f"{req.method}:{req.url} ->lambda execute")
                res = await self.lambda_manager.run_by_api(req)
                self.data_manager.sync()
                changed = self.data_manager.get_change("s3")
                updated = changed.get("updated")
                deleted = changed.get("deleted")
                while updated or deleted:
                    await self.lambda_manager.run_by_trigger(updated, deleted)
                    self.data_manager.sync()
                    s3_changed = self.data_manager.get_change("s3")
                    updated = s3_changed.get("updated", None)
                    deleted = s3_changed.get("deleted", None)
                    print("---updated---")
                    print(updated)
                    print("---deleted---")
                    print(deleted)

            elif return_val == ReturnMode.Mock:
                logger.info(f"{req.method}:{req.url} -> return mock")
                if isinstance(body, str) and  body.isdecimal() and body.isascii() and len(body)==3:
                    res = JSONResponse(status_code=int(body), content={"message": "mock response"})
                else:
                    res = response
                logger.info(f"response: status={res.status_code}, body={res.body}")
            elif return_val == ReturnMode.Example:
                res = await self.lambda_manager.example(req, status=self.return_code)
            return res
        return custom_handler


def set_mode(mode: ReturnMode, status=200):
    MediatorRoute.return_mode = mode
    MediatorRoute.return_code = status
