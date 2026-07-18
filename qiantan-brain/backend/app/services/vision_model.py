"""Abstract vision model interface — Protocol definition.

Consumers depend on this Protocol, not on any concrete implementation,
so the vision backend can be swapped (ONNX, TorchScript, cloud API, mock)
without touching application code.
"""

from __future__ import annotations

from typing import Protocol, TypedDict


class Detection(TypedDict):
    """A single product detection result from the vision model."""

    product_id: int
    name: str
    confidence: float


class VisionModelService(Protocol):
    """Abstract vision model interface.

    Every concrete implementation (ONNX, PyTorch, cloud, mock) must satisfy
    this Protocol so the vision router can work without imports of specific
    model backends.
    """

    async def recognize(self, image_bytes: bytes) -> list[Detection]:
        """Run inference on *image_bytes* and return detected products.

        Must be async — implementations SHOULD use ``run_in_executor`` to
        keep CPU-bound inference off the event-loop thread.
        """
        ...

    @property
    def model_version(self) -> str:
        """Human-readable identifier for the loaded model.

        Examples: "v1.2.0", "onnx-sha256-abc123".
        """
        ...

    @property
    def is_available(self) -> bool:
        """Whether the model loaded successfully and is ready for inference."""
        ...
