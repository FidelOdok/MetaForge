"""Firmware adapter configuration (FORGE-674, FORGE-677)."""

from __future__ import annotations

import os

from pydantic import BaseModel, Field


class FirmwareConfig(BaseModel):
    """Where the firmware tools are and how long a run may take."""

    pio: str = Field(default="pio", description="PlatformIO CLI binary name or path")
    renode: str = Field(default="renode", description="Renode binary name or path")
    qemu_xtensa: str = Field(
        default="qemu-system-xtensa", description="Espressif QEMU for the ESP32 (xtensa)"
    )
    platformio_core_dir: str = Field(
        default=os.path.expanduser("~/.platformio"),
        description="PlatformIO core dir; the installed platforms' board manifests are read here",
    )
    freertos_kernel_dir: str = Field(
        default="/opt/freertos-kernel",
        description="FreeRTOS-Kernel sources compiled into a project with a FreeRTOSConfig.h",
    )
    work_dir: str = Field(
        default="/workspace", description="Adapter workspace: projects, build output, run logs"
    )
    max_build_time: int = Field(default=600, ge=1, description="Max build time, seconds")
    max_emulation_time: int = Field(
        default=120, ge=1, description="Max wall-clock time for one emulated run, seconds"
    )

    @classmethod
    def from_env(cls) -> FirmwareConfig:
        env = os.environ
        return cls(
            pio=env.get("PIO_PATH", "pio"),
            renode=env.get("RENODE_PATH", "renode"),
            platformio_core_dir=env.get("PLATFORMIO_CORE_DIR", os.path.expanduser("~/.platformio")),
            freertos_kernel_dir=env.get("FREERTOS_KERNEL_DIR", "/opt/freertos-kernel"),
            work_dir=env.get("FIRMWARE_WORK_DIR", "/workspace"),
            max_build_time=int(env.get("FIRMWARE_MAX_BUILD_TIME", "600")),
            max_emulation_time=int(env.get("FIRMWARE_MAX_EMULATION_TIME", "120")),
        )
