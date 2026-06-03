from __future__ import annotations

import json
from typing import Any, Dict


def send_action_to_mcu(action: Dict[str, Any], port: str, baudrate: int = 115200) -> None:
    """表情头串口输出占位。

    当前主流程先把 action 保存为 outputs/last_action.json。
    后续接 Arduino/STM32 时，可以在 main.py 里调用本函数。
    """
    try:
        import serial
    except ImportError as exc:
        raise RuntimeError("串口输出需要安装 pyserial：pip install pyserial") from exc

    payload = (json.dumps(action, ensure_ascii=False) + "\n").encode("utf-8")
    with serial.Serial(port, baudrate, timeout=1) as ser:
        ser.write(payload)
