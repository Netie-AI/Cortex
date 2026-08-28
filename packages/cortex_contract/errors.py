class ContractError(RuntimeError):
    """Base contract error."""


class ValidationError(ContractError):
    pass


class UnauthorizedError(ContractError):
    pass
