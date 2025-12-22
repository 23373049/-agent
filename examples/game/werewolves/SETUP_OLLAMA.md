# 🚀 本地免费方案 - Ollama 配置指南

## 为什么选择 Ollama？

✅ **完全免费** - 无需任何 API Key
✅ **数据隐私** - 所有数据在本地处理
✅ **无限制** - 想跑多少局就跑多少局
✅ **快速稳定** - 无网络延迟，无工具调用问题

## 📋 系统要求

- **Windows 10/11**
- **至少 8GB 内存**（推荐 16GB）
- **10GB 硬盘空间**
- **可选：NVIDIA GPU**（有 GPU 会快很多，但 CPU 也能跑）

## 🔧 安装步骤（5分钟）

### 1. 下载并安装 Ollama

访问：https://ollama.com/download/windows

或直接下载：
```
https://ollama.com/download/OllamaSetup.exe
```

双击安装，默认选项即可。

### 2. 验证安装

安装完成后，打开 PowerShell：

```powershell
# 检查 Ollama 是否安装成功
ollama --version
```

应该显示版本号，如：`ollama version is 0.x.x`

### 3. 下载模型（选择一个）

#### 🌟 推荐：Qwen2.5（中文优化，7B）
```powershell
ollama pull qwen2.5:7b
```
- **下载大小**：约 4.7GB
- **推荐理由**：阿里云开源，中文理解好，速度快
- **内存需求**：8GB+

#### 备选：Llama 3.2（英文更好，3B 更小）
```powershell
ollama pull llama3.2:3b
```
- **下载大小**：约 2GB
- **推荐理由**：更小更快，适合低配机器
- **内存需求**：6GB+

#### 高性能：Qwen2.5 14B（性能更强）
```powershell
ollama pull qwen2.5:14b
```
- **下载大小**：约 9GB
- **推荐理由**：性能接近商业模型
- **内存需求**：16GB+

### 4. 启动 Ollama 服务

Ollama 安装后会自动启动后台服务。验证：

```powershell
# 测试模型是否可用
ollama run qwen2.5:7b "你好"
```

如果返回中文回复，说明成功！按 `Ctrl+D` 退出。

### 5. 配置游戏使用 Ollama

```powershell
# 设置使用 Ollama
$env:WEREWOLF_MODEL = "ollama"

# 设置模型名称（根据你下载的模型）
$env:OLLAMA_MODEL = "qwen2.5:7b"

# 运行游戏
cd "d:\桌面\开源软件\agent\-agent\examples\game\werewolves"
python main.py
```

## 🎮 快速启动脚本

创建一个 `run_ollama.ps1` 文件：

```powershell
# 设置 Ollama 配置
$env:WEREWOLF_MODEL = "ollama"
$env:OLLAMA_MODEL = "qwen2.5:7b"

Write-Host "🎮 启动狼人杀游戏（本地 Ollama 模型）" -ForegroundColor Green
Write-Host "📊 模型: qwen2.5:7b" -ForegroundColor Cyan
Write-Host "💰 成本: 完全免费！" -ForegroundColor Yellow
Write-Host ""

python main.py
```

然后运行：
```powershell
.\run_ollama.ps1
```

## ⚡ 性能优化

### 如果速度慢，可以：

1. **使用更小的模型**
```powershell
$env:OLLAMA_MODEL = "llama3.2:3b"  # 更快但效果稍差
```

2. **使用 GPU 加速**（如果有 NVIDIA 显卡）
Ollama 会自动检测并使用 GPU，无需额外配置。

3. **减少玩家数量**（临时测试）
编辑 `main.py`，将玩家数量从 9 改为 6：
```python
players = [PlayerAgent(f"Player{_ + 1}") for _ in range(6)]  # 改成 6
```

## 🆚 Ollama vs DeepSeek 对比

| 特性 | Ollama (本地) | DeepSeek (在线) |
|------|--------------|----------------|
| 成本 | ✅ 完全免费 | ⚠️ 需要付费 |
| 速度 | ✅ 快（本地） | ⚠️ 慢（网络） |
| 稳定性 | ✅ 无工具调用问题 | ❌ 频繁工具调用错误 |
| 隐私 | ✅ 完全本地 | ⚠️ 数据上传 |
| 限制 | ❌ 需要本地资源 | ✅ 无限制 |

## 🔧 故障排除

### 问题 1: "ollama command not found"
**解决**：重启 PowerShell，或添加到 PATH：
```powershell
$env:PATH += ";C:\Users\$env:USERNAME\AppData\Local\Programs\Ollama"
```

### 问题 2: 模型下载失败
**解决**：检查网络，或使用代理：
```powershell
$env:HTTP_PROXY = "http://127.0.0.1:7890"  # 如果使用代理
ollama pull qwen2.5:7b
```

### 问题 3: 运行时内存不足
**解决**：使用更小的模型
```powershell
$env:OLLAMA_MODEL = "llama3.2:3b"
```

## 📊 推荐配置

### 普通电脑（8GB 内存）
```powershell
$env:OLLAMA_MODEL = "qwen2.5:7b"  # 或 llama3.2:3b
```

### 高性能电脑（16GB+ 内存 + GPU）
```powershell
$env:OLLAMA_MODEL = "qwen2.5:14b"
```

### 低配电脑（4GB 内存）
```powershell
$env:OLLAMA_MODEL = "llama3.2:1b"  # 最小模型
```

## 🎯 开始使用

```powershell
# 1. 安装 Ollama（一次性）
# 访问 https://ollama.com/download/windows

# 2. 下载模型（一次性）
ollama pull qwen2.5:7b

# 3. 每次运行游戏
$env:WEREWOLF_MODEL = "ollama"
$env:OLLAMA_MODEL = "qwen2.5:7b"
python main.py
```

**完成！享受免费无限制的狼人杀游戏！** 🎉
