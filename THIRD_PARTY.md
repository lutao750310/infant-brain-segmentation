# 上游工具与来源

核对日期：2026-10-10。链接用于查阅官方文档及许可；上游可变化，实际运行应锁定版本。本文的方法整理与适配代码不等于第三方软件或权重的原创声明。

| 资源 | 用途 | 官方入口 |
| --- | --- | --- |
| DrawEM / MIRTK | 新生儿组织/脑区分割与配准 | [DrawEM](https://github.com/MIRTK/DrawEM) |
| DrawEM preprocess | 核对外部脑掩膜及N4缓存条件 | [preprocess.sh](https://github.com/MIRTK/DrawEM/blob/master/scripts/preprocess.sh) |
| dHCP structural pipeline | 理解容器环境和结构处理链 | [上游仓库](https://github.com/BioMedIA/dhcp-structural-pipeline) |
| ANUBEX-SA模型 | Task402新生儿脑掩膜 | [模型上游](https://github.com/ecalabr/nnUNet_models) |
| nnUNet v1 | 兼容模型推理 | [v1分支](https://github.com/MIC-DKFZ/nnUNet/tree/nnunetv1) |
| ITK-SNAP | 人工勾画及复核 | [官网](https://www.itksnap.org/) |
| 3D Slicer | 三向叠加、空间链检查 | [官网](https://www.slicer.org/) |

DrawEM上游说明其代码使用Apache-2.0，并分别列出图谱许可；代码许可不能替代图谱授权。ANUBEX权重、nnUNet、FSL和各图谱许可需分别查验，仓库不复制权重、图谱或第三方实现。

使用DrawEM时按上游要求引用其2014自动新生儿脑分割工作及2018 dHCP结构流程工作；使用ANUBEX模型时遵循模型上游最新引用说明，本仓库不编造尚未确认的论文信息。

本仓库暂未新增开放源代码许可证。公开浏览与下载不能解读为自动授予任意再许可或第三方资源使用权。
