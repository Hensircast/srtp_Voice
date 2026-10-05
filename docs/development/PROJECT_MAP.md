# 项目地图（PROJECT_MAP）

入口：`python -m tools.workbench`；`python -m tools.dsh_client`（有限派工）。命令细节见 WORKBENCH.md。

## 模块 → 职责 → 测试

| 模块 | 职责 | 测试 |
| --- | --- | --- |
| `main.py` | CLI 与同步/流式主循环、指标导出 | `tests/test_continuous.py`、`test_json_output.py` |
| `srtp_voice/config.py` | 全局配置与环境变量解析 | `tests/test_config_paths.py`、`test_env_example.py` |
| `srtp_voice/diagnostics.py` | 只读平台/依赖/设备自检 | `tests/test_diagnostics.py` |
| `srtp_voice/streaming.py` | 流式事件、TurnTiming 与 LatencyTracker | `tests/test_streaming*.py` |
| `srtp_voice/vad.py`、`audio_io.py`、`asr.py`、`streaming_asr.py` | 麦克风/VAD、增量与 final ASR | `test_vad_recording.py`、`test_asr.py`、`test_streaming_asr.py`、`test_streaming_endpoint_latency.py` |
| `srtp_voice/llm.py` | Ollama 请求、token 流和响应校验 | `test_llm_streaming.py`、`test_llm_parsing.py` |
| `srtp_voice/tts.py`、`streaming_tts.py`、`lip_sync.py` | Piper/Edge、顺序播放与口型 | `test_tts_piper.py`、`test_tts_edge.py`、`test_streaming_tts.py`、`test_lip_sync.py` |
| `srtp_voice/streaming_runtime.py`、`state_machine.py` | turn 生命周期、取消、失败恢复 | `test_streaming_runtime.py`、`test_continuous.py` |
| `srtp_voice/ser.py`、`emotion_fusion.py`、`emotion_state.py`、`memory.py` | 情绪融合、平滑与短期记忆 | `test_ser.py`、`test_emotion_fusion.py`、`test_emotion_state.py`、`test_streaming_runtime.py` |
| `tools/workbench.py` | 工作台入口与路径/脱敏公共函数 | `tests/test_workbench.py` |
| `tools/workbench_doctor.py` | 环境自检（不导入重依赖、可脱敏、可选 loopback 探测） | `tests/test_workbench.py` |
| `tools/workbench_validate.py` | 统一验证 profile（offline/full/targeted/manual） | `tests/test_workbench.py`、`test_ci_workflow.py` |
| `tools/workbench_latency.py` | 延迟基线、比较、快照 | `tests/test_workbench.py`、`test_workbench_review.py` |
| `tools/workbench_tasks.py` | 可恢复任务运行器（锁、状态、resume） | `tests/test_workbench_tasks.py` |
| `tools/dsh_client.py` | 白名单 dsh 会话客户端（status/dispatch） | `tests/test_dsh_client.py` |
| `.github/workflows/ci.yml` | Windows/Ubuntu 离线 CI（统一入口） | `tests/test_ci_workflow.py` |

## 故障 → 最小证据 → 定位

| 症状 | 最小计时/状态证据 | 先看 |
| --- | --- | --- |
| 首响长尾 | `outputs/latency-*/streaming_metrics.json` 的 `summary`（p50/p95）与 `turns` | `srtp_voice/streaming.py`、`main.py` 指标导出 |
| LLM 首 token 慢/连接失败 | `llm_first_token_ms`，脱敏 doctor 在线结果；不要先上传完整请求 | `llm.py` 的 `StrategyGenerator`、`config.py`；`test_llm_streaming.py` |
| 端点后 ASR 慢/漏 partial | 逐轮 `asr_final_ms`、`asr_first_partial_ms` 与 VAD stop 标记 | `streaming_asr.py`、`streaming_runtime.py` 的 `capture_streaming_microphone`；端点测试 |
| 句子机械/碎片、TTS 空块/除零 | sentence_ready 到 first_audio 的计时，错误类型与块序号（不含文字） | `streaming.py` 的 `SentenceChunker`、`tts.py`、`lip_sync.py`；分句/Piper 测试 |
| 串音、旧轮取消新轮/停不下来 | turn id、sequence、状态 trace、late_events | `streaming_runtime.py` 的 `StreamingTurnController`、`streaming_tts.py`；runtime/TTS 测试 |
| 流式轮次异常 | metrics 的 `late_events`、`dropped_event_history`、各 turn 状态 | `streaming.py`、`streaming_runtime.py` 的事件/队列 |
| 依赖/设备不可用 | `python -m tools.workbench doctor`（脱敏） | `tools/workbench_doctor.py`、`srtp_voice/diagnostics.py` |
| 测试/CI 失败 | `python -m tools.workbench validate --profile offline`（含退出码） | `tools/workbench_validate.py`、`.github/workflows/ci.yml` |
| 基线不可比 | 输出 JSON 的 `recording_context`、`comparison.reasons` | `tools/workbench_latency.py` |
| 任务中断/重复 | `outputs/workbench/tasks/<ID>/state.json` 的 `status`、`process_probe` | `tools/workbench_tasks.py` |
| 派工未生效/重复 | `outputs/workbench/dsh/<ID>.json` 的 `state`、`request_id` | `tools/dsh_client.py` |

## 只读核对命令

```bash
rg -n "def collect_diagnostics" srtp_voice/diagnostics.py
rg -n "class LatencyTracker|def snapshot" srtp_voice/streaming.py
rg -n "validate --profile full" .github/workflows/ci.yml
```

测试临时目录必须唯一且明确归属（本仓库测试用 `outputs/workbench/pytest-local/`）；
不读录音、对话、模型权重或凭证；本文件不需要加载历史记录即可使用。
