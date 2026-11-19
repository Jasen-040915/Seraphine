class ClientSessionClosed(ReferenceError):
    """The client session for this request has ended."""


class SummonerNotFound(BaseException):
    pass


class SummonerGamesNotFound(BaseException):
    pass


class SummonerRankInfoNotFound(BaseException):
    pass


class SummonerNotInGame(BaseException):
    pass


class RetryMaximumAttempts(BaseException):
    pass
