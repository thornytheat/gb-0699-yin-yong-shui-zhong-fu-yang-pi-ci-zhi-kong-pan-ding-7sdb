# 饮用水批次质控定义

id保留前导零；batch+analyte+pair决定平行样配对。kind为sample、duplicate、spike、blank。浓度单位mg/L、ug/L统一为mg/L；数值用Decimal。检出限Pb、As均为0.001mg/L，限定值形式<0.001不按零处理。

均值低于5倍检出限时，平行样采用绝对差<=检出限；其余采用差值/均值<=20%。双零通过绝对差规则。加标回收=(加标样-本底)/加标量×100%，80—120%通过；加标量非正失败。空白严格超过检出限才判污染。

同id最高version为生效复测，同版本重复隔离。放行优先级为空白污染、空白缺失、回收失败、加标异常、配对失败、限定值复核。每个批次每项必须有空白；受限值进入人工复核。失败检测批生成逐样复测任务和下一最低版本。

复测采用batch+analyte+id作为身份键，在此身份内选择最高version；不同检测项允许复用同样品编号。

## M8 校准核查保护放行

仪器序列字段：seq,batch,sample_id,analyte,kind,check_id,result,value。kind 取 check 或 sample；核查 result 取 PASS/FAIL，按 seq 排序，同 seq 时核查先于样品生效。

保护规则：合格(PASS)核查保护其后到下一次核查之前的样品；失败(FAIL)核查清空保护，其后样品判 UNPROTECTED；首个核查之前判 UNCHECKED。每份样品记录 protecting_check，无保护为 null。

样品状态码：PROTECTED 受保护可放行；RETEST_DONE 复测后被新合格核查覆盖；UNPROTECTED 漂移后等待证据；UNCHECKED 核查前等待证据。复测任务身份键 (batch,sample_id,analyte) 唯一，重跑同批不新增；OPEN 被合格核查覆盖后置 DONE。

不变量：核查台账 check_ledger 只追加（UNIQUE(batch,check_id)，重复 409）；放行 check_releases 只追加，逐样永久绑定 protecting_check 与 revision，重判不回收旧结论；复测 retest_tasks 状态机 OPEN→DONE。
