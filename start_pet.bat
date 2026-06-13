@echo off
rem 静默启动 Clawd 桌宠（pyw 无控制台窗口）
start "" pyw -3 "%~dp0clawd_pet.py"
