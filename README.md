# wa-wt
VROMFS和BLK文件解包器，，但此版仅兼容.json语法，优化了多线程算法，新增.obj模型导出，主体为https://github.com/Warthunder-Open-Source-Foundation/wt_ext_cli
在这里非常感谢DeepSeek Harness及DeepSeek开发人员，此项目由DeepSeek-V4.1-Flash修缮约70%
需Python 3.10+环境，依赖安装可用"C:\Users\Administrator\Desktop\Eng\war wt\tools\models\setup.bat"或python -m pip install zstandard pylzma PyQt5
命令调用语法为：
python tools\models\export_models.py --all --game-dir F:\WarThunder --out output\_full_tmp --workers auto
python tools\models\_export_clean.py --batch output\_full_tmp output\models --config output\config --workers auto
或
powershell -NoProfile -ExecutionPolicy Bypass -File "C:\Users\Administrator\Desktop\Eng\war wt\tools\all_unpack.ps1"
注意powershell 5编码格式应为UTF-8 with BOM，powershell 7可用UTF-8
本版移除了场景/建筑/植被
目前存在的问题：
顶点流声明format=14, stride=20，格式表里没有format14这一项。20 字节内要放3个顶点分量（6或12字节）+ 2个UV分量（4或8字节）【DSH完成的解析，作者暂未验证但错误存在，影响约20个同字段布局】
