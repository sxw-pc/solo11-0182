# 农险果园雹灾定损采证台

保单地块版本、冰雹影响带、落果与树冠抽样、灾后采收记录需要共同决定赔付，查勘与补录离线进行。

三套子系统通过原始证据、SQLite 状态与版本化决定互相影响。

```powershell
python -m pip install . --no-deps --no-build-isolation
python -m hailclaim init --db work.db
python -m hailclaim demo --db work.db
python -m unittest discover -s tests -v
```

命令：policy, survey, claim。每个命令通过 `--fixture` 读取单一子系统的 JSON；`demo` 依次执行正常样例。每项报告列证据 ID 和决定版本。


## 灾时版本与理赔核算

保单从已持久化版本中选择灾害事件时刻之前生效的最新版本；事后批改不追溯改变本次事故。地块矩形坐标用于面积及几何重叠检查；无几何坐标的旧记录才按地块号检查重复。品种和树龄档位共同选取合同单价。样方须在灾后采集、位于雹迹地块、树身份唯一且不是灾后补植；冲突样方冻结赔额。灾前病害样方不计雹损，但保留观察与归因。

赔额由可保面积、样方雹损率、残值、免赔和保单限额产生。付款为独立证据实体，同一付款 ID 重试不会重复入账；申诉只输出核定额与已付流水的差额。`python -m hailclaim replay --db work.db --subject case-1` 从原始证据独立重算全部十项决定并对比最新持久化报告。
