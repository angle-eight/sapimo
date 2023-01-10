class SapimoException(Exception):
    def __init__(self, message: str = ""):
        self.message = message


class LambdaInvokeError(SapimoException):
    pass


class EventConvertError(SapimoException):
    pass


class SamTemplateParseError(SapimoException):
    pass
