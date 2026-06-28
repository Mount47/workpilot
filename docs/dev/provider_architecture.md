# Provider 架构

## 设计原则

- **统一接口**：所有 LLM provider 实现同一个 `LLMProvider` ABC
- **OpenAI 兼容复用**：GLM / DeepSeek / Qwen / 阿里云百炼 都通过 `base_url` 参数复用 OpenAI provider
- **Stub 必须有**：离线测试和 CI 不能依赖网络

## Provider 清单

| Provider | SDK | 接入方式 | 阶段 |
|---|---|---|---|
| OpenAI | `openai` | 原生 SDK | Phase 4 |
| Claude | `anthropic` | 原生 SDK | Phase 5 |
| DeepSeek | `openai` | base_url: `https://api.deepseek.com` | Phase 4 |
| Qwen (通义千问) | `openai` | DashScope OpenAI 兼容接口 | Phase 4 |
| GLM (ChatGLM) | `openai` | 智谱 OpenAI 兼容接口 | Phase 4 |
| 阿里云百炼 | `openai` | 百炼平台 OpenAI 兼容接口 | Phase 4 |
| Stub | 无 | 返回固定 fixture 数据 | Phase 1 |

## 核心接口

```python
from abc import ABC, abstractmethod
from pydantic import BaseModel

class LLMProvider(ABC):
    """WorkPilot LLM Provider 抽象"""

    @abstractmethod
    def generate_structured(
        self,
        prompt: str,
        response_model: type[BaseModel],
        system_prompt: str | None = None,
        temperature: float = 0.0,
    ) -> BaseModel:
        """生成符合 response_model schema 的结构化输出"""
        ...

    @abstractmethod
    def generate_text(
        self,
        prompt: str,
        system_prompt: str | None = None,
        temperature: float = 0.0,
    ) -> str:
        """生成自由文本（用于 evidence extraction 等场景）"""
        ...
```

## 配置方式

### CLI 参数

```bash
workpilot run --provider deepseek --model deepseek-chat ...
```

### 环境变量

```bash
export WORKPILOT_PROVIDER=deepseek
export WORKPILOT_MODEL=deepseek-chat
export DEEPSEEK_API_KEY=sk-xxx
```

### 配置文件（可选）

```yaml
# workpilot.yaml
provider:
  name: deepseek
  model: deepseek-chat
  api_key: ${DEEPSEEK_API_KEY}
  base_url: https://api.deepseek.com
  temperature: 0.0
  max_tokens: 4096
```

## Provider 注册

```python
# src/workpilot/providers/registry.py

PROVIDERS = {
    "openai": OpenAIProvider,
    "claude": ClaudeProvider,
    "deepseek": DeepSeekProvider,
    "qwen": QwenProvider,
    "glm": GLMProvider,
    "bailian": BailianProvider,
    "stub": StubProvider,
}

def get_provider(name: str, **kwargs) -> LLMProvider:
    """按名称获取 provider，注入 api_key/model/base_url 等配置"""
    ...
```

## OpenAI 兼容 Provider 复用

DeepSeek / Qwen / GLM / 百炼 均可通过 OpenAIProvider 以不同 `base_url` 接入：

```python
# DeepSeek
provider = OpenAIProvider(
    api_key="sk-xxx",
    model="deepseek-chat",
    base_url="https://api.deepseek.com/v1",
)

# Qwen (DashScope)
provider = OpenAIProvider(
    api_key="sk-xxx",
    model="qwen-plus",
    base_url="https://dashscope.aliyuncs.com/compatible-mode/v1",
)

# GLM
provider = OpenAIProvider(
    api_key="xxx",
    model="glm-4",
    base_url="https://open.bigmodel.cn/api/paas/v4",
)

# 百炼
provider = OpenAIProvider(
    api_key="sk-xxx",
    model="qwen-plus",
    base_url="https://dashscope.aliyuncs.com/compatible-mode/v1",
)
```

## Malformed Response 处理

所有 provider 必须处理：

- JSON 解析失败 → 重试一次，仍失败则报错
- 结构不匹配 response_model → 用 pydantic 校验，报具体字段错误
- 空响应 / 超时 → 记入 trace，fail closed
- token 超限 → 截断提示并重试（或降级到更小的 context 窗口）

## 测试策略

- **单元测试**：所有 provider 用 `responses` 或 `respx` mock HTTP
- **集成测试**：Stub provider 贯穿全链路，不依赖网络
- **CI**：只跑 stub provider，真实 provider 测试标记 `@pytest.mark.integration` 跳过
