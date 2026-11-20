# 智能家居组合兼容证据与销售承诺

这是用于智能家居兼容性资料分析的本地工作区。样例资料为合成数据，不代表真实设备或用户记录。工作区包含 Python 3.11+ 命令入口和 JSON 记录，可离线查看与核验。

## 运行

无需安装第三方依赖。设置 `PYTHONPATH=src` 后运行 `python3 -m compatibility-sessions` 可查看工作区版本。运行现有基线检查：`PYTHONPATH=src python3 -m unittest discover -s tests -v`。

## 分析命令

所有命令只读本地记录，不控制真实设备；`--data` 指定资料目录（默认 `data/`），`--json` 输出机器可读结果。

- `python3 -m compatibility-sessions report`：完整放量建议（家庭批次 + 共享站点 + 已售套装）。
- `python3 -m compatibility-sessions households`：家庭批次分析——按服务优先级分配窗口工时与替换件，给出每户的前进路径、安全退回或保留原组合的结论，并区分某个中间阶段缺证、当前已无可行转换、有路径但伙伴容量不足。
- `python3 -m compatibility-sessions site`：共享网关站点视图——按整个共享域判断连续可用，列出连接现状、端口占用、各户当前承诺、候选操作的许可/证据/容量阻塞、复验与许可补齐分别改变哪条路径。
- `python3 -m compatibility-sessions bundle <编号>`：已售套装客服视图——当时的承诺、已执行步骤、现在仍可兑现的安排、何时改走人工支持（如 `SB11`，站点套装如 `PB43`）。

分析口径：已执行的步骤是家庭真实现状；已预留未开始的安排只占显式释放才回到公用池；同一安装反复登记只算一次独立观测；更正不重写原始交付记录，只在分析视图里另发新的支持范围；证据不跨拓扑、配套、临时状态或接入数量继承。

## 资料

`data/sample.json` 保存小型合成记录，顶层键分别为 models, firmware, trials, commitments。

`data/maintenance_windows.json` 提供维护批次的现场导出。`states` 列的是家庭内各部件实际版本或临时替代件；`changes` 的方向来自装配条件，未列反向就不属于可现场执行的回退。`protocol` 定义功能的必需阶段和独立完整安装门槛；空值为未观察，只有同一安装的必需阶段均完成才算完整。`trials` 的范围以状态、拓扑和功能为准，本批没有跨拓扑可转用声明。`windows` 的分钟为每支服务组当窗工作时间，`crew` 是组数，`loaner_bell` 是该窗可分配的替换件数；进入临时状态的替换件随户保留至离开。`priority` 数值越小服务等级越高。`dispatches` 记录过去已执行的变更和预留的未来选择，已执行的分钟从相应窗口扣除。`corrections` 中只更改列出的试验字段，不是新的安装。`retrial_slots` 仅为可安排的复验条件，尚无测量结果。

`data/partner_calendar.json` 补充维护窗口的起止时刻、临时替换件的实体编号、实际工时与旧出售记录。预约从 `from_window` 开始占用实物，到 `earliest_release` 之前不得调走。`allowed_reversible_transitions` 是伙伴另外确认可以回退的设备组合，仅限该家庭，和普通变更清单一并查阅；`last_window` 是允许回退的最后一个窗口。试验结果沿用维护登记中的阶段字段。

`data/shared_gateway.json` 是独立共享站点。沿用 `maintenance_windows.json` 的功能协议与阶段，但该站点的部件、作业和窗口以本网关台账为准。`households_connected` 是同一网关上实际接入的家庭数，包括自身；共享试验以网关安装 `installation` 为独立观测，不因服务多户而成为多次安装。未声明跨接入数量、拓扑或临时设备适用。共享操作的配置和时长以 `operations` 登记；`permissions` 的操作种类与窗口是授权范围，没列即未授权。`work_orders` 记录已实际发出的任务，`receipts` 是可用于确认真实连接关系的回执；`inventory_notices` 区分实际生效与支持人员知悉时刻。`scope_rechecks` 是仍未执行的复验条件，不是现成证据。临时设备每户使用一个端口；离开设备后才释放。
