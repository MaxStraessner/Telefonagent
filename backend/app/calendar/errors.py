class CalendarError(Exception):
    def __init__(self, code: str, message: str, *, transient: bool = False):
        super().__init__(message)
        self.code = code
        self.message = message
        self.transient = transient


class CalendarProviderError(CalendarError):
    pass


class CalendarConfigurationError(CalendarError):
    pass
