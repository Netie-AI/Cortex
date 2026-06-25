# GPU / local inference setup (RTX 4070, CUDA 13.2)
# Run from repo root: .\scripts\setup_gpu_env.ps1

$ErrorActionPreference = "Stop"
$Root = Split-Path -Parent (Split-Path -Parent $MyInvocation.MyCommand.Path)
Set-Location $Root

$Venv = Join-Path $Root ".venv_gpu"
if (-not (Test-Path $Venv)) {
    Write-Host "Creating .venv_gpu..."
    python -m venv $Venv
}

$Py = Join-Path $Venv "Scripts\python.exe"
$Pip = Join-Path $Venv "Scripts\pip.exe"

Write-Host "Upgrading pip..."
& $Py -m pip install -U pip wheel

Write-Host "Installing PyTorch CUDA 13.2..."
& $Pip install torch torchvision --index-url https://download.pytorch.org/whl/cu132

Write-Host "Installing Cortex train/inference extras..."
& $Pip install -e ".[dev,api,dms,gpu,local-inference]"

Write-Host "Optional: transformers + peft for Qwen fine-tune..."
& $Pip install transformers peft accelerate datasets bitsandbytes

Write-Host ""
Write-Host "Verify GPU:"
& $Py -c "import torch; print('cuda:', torch.cuda.is_available()); print(torch.cuda.get_device_name(0) if torch.cuda.is_available() else 'cpu')"

Write-Host ""
Write-Host "Enable local classify inference:"
Write-Host '  $env:CORTEX_LOCAL_INFERENCE = "1"'
Write-Host '  $env:CORTEX_LOCAL_MODEL = "Qwen/Qwen2.5-1.5B-Instruct"'
Write-Host ""
Write-Host "Reuse OpenForge Qwen 7B cache if present:"
Write-Host "  Copy HF cache from C:\Users\user\.cache\huggingface or set HF_HOME"
