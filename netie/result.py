from dataclasses import dataclass, field
from typing import TypeVar, Generic, Any

T = TypeVar('T')

@dataclass
class Ok(Generic[T]):
    value: T

@dataclass
class Err:
    code: str
    message: str
    detail: dict[str, Any] = field(default_factory=dict)

Result = Ok[T] | Err

E001 = "E001"
E002 = "E002"
E003 = "E003"
E004 = "E004"
E005 = "E005"
E006 = "E006"
E007 = "E007"
E008 = "E008"
E009 = "E009"
E010 = "E010"

API_KEY_MISSING = E002
SYNTHESIS_FAILED = E003
INVALID_DSL = E004
CYCLIC_DAG = E005
INJECTION_DETECTED = E006
MANIFEST_VIOLATION = E007
TIER_ROUTING_ERROR = E008
WASM_EXECUTION_ERROR = E009
PLATFORM_ERROR = E010
