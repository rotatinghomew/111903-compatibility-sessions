# 智能家居组合兼容证据与销售承诺

这是用于智能家居兼容性资料分析的本地工作区。样例资料为合成数据，不代表真实设备或用户记录。工作区包含 Python 3.11+ 命令入口和 JSON 记录，可离线查看与核验。

## 运行

无需安装第三方依赖。设置 `PYTHONPATH=src` 后运行 `python3 -m compatibility-sessions` 可查看工作区版本。运行现有基线检查：`PYTHONPATH=src python3 -m unittest discover -s tests -v`。

## 资料

`data/sample.json` 保存小型合成记录，顶层键分别为 models, firmware, trials, commitments。
