# wa-wt
VROMFS and BLK file unpacker, but this version only supports .json syntax, optimizes multi-threading algorithm, adds .obj model export, mainly based on https://github.com/Warthunder-Open-Source-Foundation/wt_ext_cli

Special thanks to DeepSeek Harness and DeepSeek developers, this project is about 70% repaired by DeepSeek-V4.1-Flash

Requires Python 3.10+ environment, dependencies can be installed using "C:UsersAdministratorDesktopEngwar wttoolsmodelssetup.bat" or python -m pip install zstandard pylzma PyQt5

Command usage syntax:

python toolsmodelsexport_models.py --all --game-dir F:WarThunder --out output_full_tmp --workers auto

python toolsmodels_export_clean.py --batch output_full_tmp outputmodels --config outputconfig --workers auto

or

powershell -NoProfile -ExecutionPolicy Bypass -File "C:UsersAdministratorDesktopEngwar wttoolsall_unpack.ps1"

Note: PowerShell 5 encoding format should be UTF-8 with BOM, PowerShell 7 can use UTF-8

This version has removed scenes/buildings/vegetation

Current issues:

Vertex stream declaration format=14, stride=20, format14 is not listed in the format table. Within 20 bytes, need to place 3 vertex components (6 or 12 bytes) + 2 UV components (4 or 8 bytes) [Parsed by DSH, the author has not verified but the error exists, affecting about 20 fields of the same layout]
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
