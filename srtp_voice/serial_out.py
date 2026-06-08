from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Dict


def build_serial_packet(action: Dict[str, Any], lip_sync: Dict[str, Any]) -> Dict[str, Any]:
    return {
        "type": "robot_head_action",
        "version": "v1.0",
        "expression": action.get("expression", "neutral"),
        "gaze": action.get("gaze", "look_at_user"),
        "blink": action.get("blink", "natural"),
        "servo_targets": action.get("servo_targets_placeholder", {}),
        "lip_sync": lip_sync,
    }


def save_serial_packet(packet: Dict[str, Any], output_path: Path) -> None:
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(
        json.dumps(packet, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )


def send_serial_packet(packet: Dict[str, Any], port: str, baudrate: int = 115200) -> bool:
    try:
        import serial
    except ImportError:
        print("[SERIAL] 未安装 pyserial，仅保存动作文件")
        return False

    try:
        payload = json.dumps(packet, ensure_ascii=False) + "\n"
        with serial.Serial(port, baudrate, timeout=1) as ser:
            ser.write(payload.encode("utf-8"))
        print(f"[SERIAL] 已发送到 {port}")
        return True
    except Exception as exc:
        print(f"[SERIAL] 串口发送失败：{exc}")
        return False