# 具体实现步骤

本文是公开研究方法说明。标为“已测试”的是本仓库核查代码与合成测试；ANUBEX推理及DrawEM命令是供兼容环境核验后使用的适配步骤，不代表已在任意新机器上完成端到端验证。本次发布不执行真实数据分割。

## 0. 环境与输出边界

核查程序：Python >=3.10，NumPy、SciPy、nibabel、Matplotlib；依赖写在 `pyproject.toml`。分割另需ANUBEX-SA权重、nnUNet v1、匹配的PyTorch/CUDA（GPU推理时）、MIRTK/DrawEM、FSL及对应图谱；它们不随仓库分发，也不因安装核查包而自动安装。

可从 [DrawEM上游](https://github.com/MIRTK/DrawEM) 和 [ANUBEX模型上游](https://github.com/ecalabr/nnUNet_models)准备软件资源。固定软件提交或镜像摘要、模型ZIP和解压文件校验和，分别检查代码、模型、图谱许可。不要全局关闭不可信模型的反序列化保护；旧权重只在核验来源、隔离且兼容的环境中加载。

一种可用的研究目录布局如下，所有例名仅为占位符：

```text
study/
  data/original/case_T2.nii.gz       # 保留，只读
  data/input/case.nii.gz            # 全视野的统一处理网格
  masks/case.nii.gz                 # 候选脑掩膜，0/1
  work/A-case/                     # BET基线，独立目录
  work/B-case/                     # 掩膜通过门槛后创建
  qc/                             # 全图、局部、连续邻层
  audit/                          # 体积/比较/校验和
```

## 1. 输入、年龄与空间检查

从合法授权的数据源取得3D T2及扫描时PMA。PMA是扫描时的孕周口径，不等同于出生后日龄；定义和单位必须在研究方案中一致。连续PMA先验证在28–44周内，再按预先声明的整数取整规则选模板（示例使用最近整数、半整数取偶）。超出范围不要为运行成功而截断。

如需统一1mm，单独生成处理副本，记录原体素尺寸、目标网格和灰度插值；不改写采集原图。倾斜采集不等于错误，不能只改affine把歪头“摆正”。A/B分割和参考标注必须落到同一物理空间；重采样标签只能用最近邻或等价离散标签方式。

检查全脑覆盖、左右方向、有限affine、毫米单位和有效qform/sform是否一致。核查程序拒绝冲突和网格不一致；不会偷偷对齐。没有空间单位时应追查来源，仅在独立确认后才显式声明 `assume_mm`。

## 2. 运行独立脑提取

本文配置面向ANUBEX-SA的Task402、nnUNet v1分支。上游ZIP名为 `Task402_neonatal_SA_brainmask.zip`，解压的任务目录可能使用不同名称；以实际目录为准，不猜测或改名拼凑。模型上游明确使用nnUNet v1，不能直接混用v2命令。[模型与安装说明](https://github.com/ecalabr/nnUNet_models)

在兼容、隔离的nnUNet环境中先安装自己取得并校验的模型：

```bash
nnUNet_install_pretrained_model_from_zip /path/to/verified-model.zip
```

本仓库的适配脚本直接使用v1 `predict_cases` 接口，参数固定为五折集成、TTA、step_size=0.5、mixed precision、seed=0、禁用默认后处理，并记录输入/输出摘要。指定的是完整trainer目录，不是ZIP路径：

```bash
python scripts/predict_brain_mask.py \
  --input /path/to/study/data/input/case.nii.gz \
  --model-directory /path/to/models/3d_fullres/Task402_neonatal_MA_brainmask/nnUNetTrainerV2__nnUNetPlansv2.1 \
  --output /path/to/study/mask-inference-case
```

新输出目录中包括 `brain_mask.nii.gz` 和 `run.json`。脚本拒绝已有输出目录、非二值/空掩膜以及与输入不一致的空间。它不是经过所有环境验证的模型启动器，参数接口需与已安装的nnUNet版本一致；本次只做脚本帮助、语法及空间检查测试，不下载权重或启动GPU推理。seed固定也不保证GPU逐位确定性。

## 3. 掩膜质量复核

用3D Slicer或ITK-SNAP打开完整T2与掩膜；确认背景是个体T2、掩膜是真正标签，而不是把模板当背景。三向连续看眼眶/面颈排除、脑缘覆盖、后枕、小脑/脑干与CSF范围。下游组织定义与掩膜必须兼容；外部小岛可以提示错误，但不能仅凭最大连通域规则删除解剖结构。条目见 [QC_CHECKLIST.md](QC_CHECKLIST.md)。

掩膜不合格则暂停、记录、修复并再次复核。比较新旧方案时开发集与留出集分开，保持同一输入和尽可能相同的年龄、线程与软件版本，不根据留出集反复调参。

v0.2提供[人工与Agent辅助复核包](MASK_REVIEW.md)。`mask-qc --mode human|agent`共用检查与出图，三组原生平面覆盖全部切片；`record-mask-review`核对查看声明和证据引用；`decide-mask-qc`保存人工探索性决定。版本化提示词与Schema随安装包分发。

Agent需在已有且获授权的视觉会话中逐页看图，本工具不调用模型或上传影像。具名记录是本地自报，不是医院账户认证。后续准备计划可用`--mask-qc-decision`绑定审核；没有决定的草案明确未批准，全部计划均不执行分割。

## 4. 新工作目录中运行DrawEM

DrawEM上游预处理在脑掩膜文件已存在时跳过BET；同时已存在的N4文件也可能使预处理跳过，所以**只换掩膜、复用旧目录不是完整重跑**。务必核对安装版本的脚本条件，而不只相信文件名。[上游预处理源码](https://github.com/MIRTK/DrawEM/blob/master/scripts/preprocess.sh)

下面是人工门槛通过后的操作模板。`work/B-case`必须全新，输入文件基名为 `case`，掩膜名与之严格匹配；示例年龄40周是占位数，不是任一患者年龄。

```bash
mkdir /path/to/study/work/B-case
mkdir /path/to/study/work/B-case/segmentations
cp -n /path/to/study/mask-inference-case/brain_mask.nii.gz \
  /path/to/study/work/B-case/segmentations/case_brain_mask.nii.gz

mirtk neonatal-segmentation /path/to/study/data/input/case.nii.gz 40 \
  -a ALBERT -d /path/to/study/work/B-case -t 8 -c 0 -p 0 -v 1
```

命令需在FSL/MIRTK/DrawEM已配置好的环境中执行。N4、组织先验、多图谱配准、EM与后处理应全部重新生成。ALBERT和M-CRIB等标签定义不能混用；输出常见 `case_all_labels.nii.gz`、`case_tissue_labels.nii.gz` 和脑掩膜，具体以上游版本为准。[DrawEM运行与输出说明](https://github.com/MIRTK/DrawEM)

如建立A基线，在另一个空目录不预置自定义掩膜，让上游BET产生掩膜；其余配置尽量相同。记录两臂所有差异，不能把整条多参数变化叫作严格单变量实验。

本仓库的 `neonatal-audit plan-drawem`可生成只读输入、独立工作目录的准备计划，需要你提供合法核验的Docker摘要：

```bash
neonatal-audit plan-drawem --image /path/to/study/data/input/case.nii.gz \
  --mask /path/to/study/mask-inference-case/brain_mask.nii.gz \
  --pma 40.0 --workdir /path/to/study/work/fresh-docker-case \
  --image-digest sha256:YOUR_VERIFIED_64_HEX_DIGEST --output audit/drawem-plan
```

其中摘要是必须替换的占位符，直接运行会被拒绝。计划中的容器路径属于特定dHCP环境模板，需逐项核验入口、FSL配置及掩膜预置条件；不是通用镜像拉取地址。计划不执行Docker、分割或批量队列。

## 5. 区域级质量检查

先核对全脑位置及左右，再按组织和脑区看连续邻层。全图保留解剖上下文，局部裁剪只作显示，不能更改计算标签；同位置、同窗宽窗位才能公平比较。标签在模板上好看但在个体眼眶/头皮上漏入就是问题；程序退出0不构成通过。

海马等小结构需要统一头/体/尾、与杏仁核和旁海马组织的分界；人工参考应记录协议、完整性、是否参考自动轮廓、标注者和复标情况。不将不确定练习勾画当金标准。异常解剖按研究方案另行裁决，禁止强制左右对称。

## 6. 体积计算与比较

体积 = 该标签组的体素数 × `abs(det(affine[:3,:3])) / 1000`，单位mL；同时输出pixdim历史口径，便于识别差异。最终标签在个体网格中计量，不能直接用变形后模板空间体积替代个体体积。

```bash
neonatal-audit volumes \
  --labels /path/to/study/work/B-case/segmentations/case_all_labels.nii.gz \
  --output audit/B-volumes
```

**仅用于本仓库冻结DHF87分组**。即使标签数值落在0–87，也不能证明标签含义正确；运行前核对对应图谱查找表。不要用M-CRIB或仅含海马1/2的人工标签套用全脑分组。

编辑 `examples/compare.json` 中路径和标签定义（相对配置目录），然后：

```bash
neonatal-audit compare --config examples/compare.json --output audit/hippocampus-comparison
```

输出 `comparison.csv`、`comparison.json`、`report.html`、输入校验及可选的三张原生索引平面图。默认代表平面不保证每侧ROI都在同一图中可见，完整验收仍需独立连续三向浏览；不能仅用这三张图替代临床复核。显示平面可能为斜位，不自动宣称解剖轴位/冠状/矢状。

## 7. 归档与通过标准

每次保留输入/掩膜/输出SHA、空间信息、软件与模型版本、图谱/标签定义、连续PMA/取整年龄、资源设置及复核记录。分开记录“技术通过”“脑区通过”“病变排除”和“研究纳入”；未经研究者/医生确认，候选体积不覆盖正式数据。

本次公开发布的证据是核查程序的合成测试与受控软件测试，不提供患者级成绩、准确率或全队列QC提升幅度。资源估计需按本地硬件、输入分辨率和实现实测，不能保证固定时间完成。
