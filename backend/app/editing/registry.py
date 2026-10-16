from typing import get_args

from app.editing.operation_contracts import Operation

# The discriminated request union is also the sole source of advertised commands.
OPERATION_MODELS = get_args(get_args(Operation)[0])
OPERATIONS = tuple(get_args(model.model_fields["op"].annotation)[0] for model in OPERATION_MODELS)
TEMPLATE_OPERATIONS = tuple(name for name in OPERATIONS if name != "ocr.set_value")
