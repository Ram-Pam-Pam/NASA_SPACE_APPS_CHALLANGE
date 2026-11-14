from pydantic import BaseModel, Field


class RiskParameters(BaseModel):
    """Tunable parameters of the risk analysis (all have sensible defaults)."""

    segment_length_m: float = Field(100, gt=0, le=5000, description="Length of infrastructure segments")
    hazard_influence_m: float = Field(200, gt=0, description="Distance at which hazard influence fades to zero")
    insar_buffer_m: float = Field(100, gt=0, le=1000, description="Radius around a segment for collecting InSAR points")
    velocity_field: str = Field("mean_velocity", description="Attribute with LOS velocity [mm/yr] in deformation layers")
    velocity_saturation_mm_yr: float = Field(20, gt=0, description="Velocity treated as maximum danger")
    hazard_weight: float = Field(0.4, ge=0, description="Weight of hazard-zone proximity in the score")
    motion_weight: float = Field(0.6, ge=0, description="Weight of InSAR ground motion in the score")