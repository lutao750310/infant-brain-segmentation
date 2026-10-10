# 脑掩膜人工与Agent辅助复核

第3步检查B路线的ANUBEX-SA脑掩膜能否作为后续DrawEM输入。v0.2提供离线证据工作流：程序检查空间并出图，人工或已有视觉Agent回填结果，再由具名人工作探索性处理决定。它不调用模型API，不修改掩膜，不运行DrawEM，也不授予临床验收或研究纳入资格。

## 安装和合成演示

在仓库根目录执行：

```bash
python -m pip install -e .
python -m unittest discover -s tests -v
neonatal-audit mask-qc-demo --mode agent --output runs/mask-qc-synthetic
```

打开runs/mask-qc-synthetic/packet/report.html。示例只有合成强度、椭球和孤立小岛；没有患者资料或实际AI推理。human模式可使用新输出目录另行演示。协议单元测试不能证明AI看图准确。

## 选择复核方式

| 方式 | 第一轮复核者 | 模型调用 | 后续决定 |
| --- | --- | --- | --- |
| human | 人查看生成图与Slicer或ITK-SNAP原图 | 无 | 人填写独立决定 |
| agent | 操作者让获授权的已有视觉Agent逐页看图 | 本程序不调用模型，由已有会话完成 | 人仍需独立查看和确认 |

两种方式共用检查、出图和审核标准，不是两套分割算法。纯文本模型不能执行视觉复核；缺少读图能力应返回needs_human_review，而非根据数值猜测。

## 生成证据包

```bash
neonatal-audit mask-qc \
  --image /path/to/study/T2/case.nii.gz \
  --mask /path/to/study/masks/case.nii.gz \
  --mode agent --output /path/to/study/qc/case-packet
```

输入须为同网格、毫米单位的3D T2及非空0/1掩膜。网格差异、冲突qform/sform、非有限值和已有输出目录会被拒绝；不自动阈值化、重采样、删小岛或膨胀。

packet.json保存输入SHA、几何、运行版本、技术警告和图片清单；images覆盖三组原生索引平面的**全部切片**，包括没有掩膜的层面。report.html并列原图与边界，搭配全图、局部和连续邻层。默认每页5层，--planes-per-page允许1至6层。

原生平面可能倾斜，不能自动称为解剖轴位、冠状或矢状。局部框跟随掩膜，遗漏可能在框外，必须查看全图。生成全部图片不等于全部已看；边缘接触、多个连通域、体积仅提示复核位置，不直接给解剖结论。

## 按提示词回填

[版本化提示词](src/neonatal_seg_audit/mask_review_prompt.md)和[JSON输出规范](src/neonatal_seg_audit/mask_review_schema.json)随安装包分发，证据包保存副本及SHA。

向已有视觉会话提供prompt.md、packet.json和response.schema.json，**分批逐页**打开图片，保留实际查看范围和全部疑点。不只用一张缩略图验收；图太小或需重新定向时在Slicer或ITK-SNAP补看，不能猜测通过。

复制response.template.json到新文件response.json后填写真实复核者、Agent的实际模型版本、实际查看图片ID及六项检查：脑覆盖、眼眶/头皮、面颈、小脑/脑干、CSF兼容范围、孤立连通域。每个可判断条目和疑点引用已查看图片；定位用原网格IJK，不清楚留null。模板默认未判断，不能直接当作已完成。

suggest_pass要求声明全部页面已看、六项均no_visible_problem且无未决疑点；否则使用needs_human_review，明确不可用可选technical_fail。clinical_acceptance必须为false。程序无法独立证明实际看图，查看覆盖是自报记录，不是认证。

```bash
neonatal-audit record-mask-review \
  --packet /path/to/study/qc/case-packet/packet.json \
  --response /path/to/study/qc/response.json \
  --output /path/to/study/qc/case-review
```

程序校验Schema、六项完整性、图片引用、IJK范围与引用平面、输入/证据/提示词SHA，保存review.json及未批准的decision.template.json。漏看不能建议通过，未知图片和临床验收字段会被拒绝。

## 人工决定

人独立查看原图和证据，将决定模板复制为human-decision.json，填写具名角色、说明、实际查看页面、确认六项及决定。仅限approve_for_exploratory_drawem、request_more_review、reject。批准要求完整清晰的前轮建议和人工完整查看声明。AI有未决疑点不能直接抹去；补看、修复后新建包再复核，或另建human模式包记录不同判断及依据。

```bash
neonatal-audit decide-mask-qc \
  --packet /path/to/study/qc/case-packet/packet.json \
  --review /path/to/study/qc/case-review/review.json \
  --decision /path/to/study/qc/human-decision.json \
  --output /path/to/study/qc/case-decision
```

输出decision.json只批准“此掩膜进入探索性DrawEM准备”，不是海马等脑区通过。准备计划接入方式：

```bash
neonatal-audit plan-drawem \
  --image /path/to/study/T2/case.nii.gz \
  --mask /path/to/study/masks/case.nii.gz \
  --workdir /path/to/study/work/B-case --pma 40 --threads 8 \
  --image-digest sha256:YOUR_VERIFIED_64_HEX_DIGEST \
  --mask-qc-decision /path/to/study/qc/case-decision/decision.json \
  --output /path/to/study/qc/case-drawem-plan
```

摘要和年龄是须核验替换的示例；命令只生成计划。审核链或图像/掩膜字节不匹配会报错。不提供决定时，兼容旧接口但草案明确ready_for_exploratory_drawem=false；无效决定不会降级为通过。输入、证据、提示词或记录变更后重新复核。

## 权限与验证边界

具名决定是本地自报和哈希审计，不是医院账户认证、数字签名或不可篡改存储，不能防止有写权限者冒充人或同时改写整个记录，也不能拦截用户自行运行DrawEM。所有准备计划均不执行分割。

默认影像、报告和逐病例记录留在本地，公开GitHub只放代码、通用提示词和合成测试。真实MRI与派生截图可能属于敏感医学资料；使用云端视觉模型需另行明确授权与合规安排，本工具不提供上传权限。

AI性能须另用独立医生复核样本评估漏报、误报、拒答及不确定性后，才考虑扩大自动化。完整出图、AI建议和人工技术确认均不能替代临床边界验收。
