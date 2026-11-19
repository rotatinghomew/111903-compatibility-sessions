# 智能家居组合兼容证据与销售承诺

这是用于智能家居兼容性资料分析的本地工作区。样例资料为合成数据，不代表真实设备或用户记录。工作区包含 Python 3.11+ 命令入口和 JSON 记录，可离线查看与核验。

## 运行

无需安装第三方依赖。设置 `PYTHONPATH=src` 后运行 `python3 -m compatibility-sessions` 可查看工作区版本。运行现有基线检查：`PYTHONPATH=src python3 -m unittest discover -s tests -v`。

## 资料

`data/sample.json` 保存小型合成记录，顶层键分别为 models, firmware, trials, commitments。

`data/maintenance_windows.json` 提供维护批次的现场导出。`states` 列的是家庭内各部件实际版本或临时替代件；`changes` 的方向来自装配条件，未列反向就不属于可现场执行的回退。`protocol` 定义功能的必需阶段和独立完整安装门槛；空值为未观察，只有同一安装的必需阶段均完成才算完整。`trials` 的范围以状态、拓扑和功能为准，本批没有跨拓扑可转用声明。`windows` 的分钟为每支服务组当窗工作时间，`crew` 是组数，`loaner_bell` 是该窗可分配的替换件数；进入临时状态的替换件随户保留至离开。`priority` 数值越小服务等级越高。`dispatches` 记录过去已执行的变更和预留的未来选择，已执行的分钟从相应窗口扣除。`corrections` 中只更改列出的试验字段，不是新的安装。`retrial_slots` 仅为可安排的复验条件，尚无测量结果。
