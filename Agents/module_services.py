from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from .presentation_adapter import DisplayBlock
from .scene_catalog import validate_module_key


@dataclass(frozen=True)
class DirectModuleServiceResult:
    active_agent: str
    askmore: str
    blocks: list[DisplayBlock]


def supports_direct_module_service(module_key: str) -> bool:
    return validate_module_key(module_key) in {
        "compliance_scanner",
        "contract_templates",
        "communication_guide",
    }


def run_direct_module_service(module_key: str, user_input: str, attachments_meta: Any | None = None) -> DirectModuleServiceResult:
    normalized = validate_module_key(module_key)
    text = str(user_input or "").strip()
    if not text:
        raise ValueError("user_input must be non-empty for module service")
    if normalized == "compliance_scanner":
        return _run_compliance_scanner(text, attachments_meta)
    if normalized == "contract_templates":
        return _run_contract_templates(text, attachments_meta)
    if normalized == "communication_guide":
        return _run_communication_guide(text, attachments_meta)
    raise ValueError(f"unsupported direct module service: {normalized}")


def _attachment_names(attachments_meta: Any | None) -> list[str]:
    if not isinstance(attachments_meta, dict):
        return []
    files = attachments_meta.get("files")
    if not isinstance(files, list):
        return []
    names: list[str] = []
    for item in files:
        if not isinstance(item, dict):
            continue
        name = str(item.get("name") or "").strip()
        if name:
            names.append(name)
    return names


def _module_header(title: str, summary: str, module_key: str) -> DisplayBlock:
    return DisplayBlock(
        kind="result_card",
        title=title,
        text=summary,
        metadata={"module_key": module_key, "card_variant": "summary"},
    )


def _module_card(title: str, text: str, items: list[str], module_key: str, variant: str) -> DisplayBlock:
    return DisplayBlock(
        kind="result_card",
        title=title,
        text=text,
        items=items,
        metadata={"module_key": module_key, "card_variant": variant},
    )


def _run_compliance_scanner(user_input: str, attachments_meta: Any | None) -> DirectModuleServiceResult:
    text = user_input.lower()
    stage = "在职管理"
    if any(token in text for token in ["招聘", "入职", "试用"]):
        stage = "招聘入职"
    elif any(token in text for token in ["解除", "裁员", "离职", "辞退"]):
        stage = "解除/终止劳动关系"

    risk_point = "规章制度与用工流程"
    if any(token in text for token in ["加班", "工时", "考勤"]):
        risk_point = "工时与加班管理"
    elif any(token in text for token in ["社保", "公积金"]):
        risk_point = "社保公积金缴纳"
    elif any(token in text for token in ["工伤", "受伤", "事故"]):
        risk_point = "工伤与职业安全"

    if risk_point == "工时与加班管理":
        level = "高风险"
        findings = [
            "检查是否存在未经审批但长期默许的延时工作安排。",
            "确认考勤、审批、调休和工资条之间的数据能否闭环对应。",
            "排查管理人员口头安排加班但未留痕的场景。",
        ]
        actions = [
            "统一加班申请、审批、复核、支付四段留痕。",
            "对无法安排补休的岗位明确工资折算口径。",
            "将加班制度完成公示并保留签收/已读记录。",
        ]
    elif risk_point == "社保公积金缴纳":
        level = "高风险"
        findings = [
            "核对实际工作地、参保地与劳动合同约定是否一致。",
            "排查试用期不缴、按最低基数统一缴纳等高频违规点。",
            "确认离职、调岗、异地派驻时的停缴和续缴手续。",
        ]
        actions = [
            "按实际工资和当地规则复核基数，形成月度复核台账。",
            "对历史欠缴期间制定补缴计划和员工通知模板。",
            "将参保规则写入入职和异动流程。",
        ]
    elif risk_point == "工伤与职业安全":
        level = "高风险"
        findings = [
            "确认事故报告、送医、现场记录、证人信息是否能在24小时内固化。",
            "核对是否建立工伤申报、停工留薪、复工鉴定的标准流程。",
            "排查外勤、驻场、团建活动等边界场景的识别规则。",
        ]
        actions = [
            "建立事故发生当日的标准取证清单。",
            "为HR和用工部门配置工伤申报时间节点提醒。",
            "对高风险岗位追加培训签到和安全告知留痕。",
        ]
    else:
        level = "中高风险"
        findings = [
            "确认制度是否经过民主程序并完成公示。",
            "排查日常执行是否与书面制度一致。",
            "检查招聘、入职、异动、离职文书是否存在空白或版本混用。",
        ]
        actions = [
            "梳理制度版本、审批记录、公示记录三份底稿。",
            "统一人事操作节点的签收和确认文案。",
            "抽样复核近三个月典型用工流程。",
        ]

    attachments = _attachment_names(attachments_meta)
    blocks = [
        _module_header(
            "合规风险扫描结果",
            f"阶段：{stage}｜风险域：{risk_point}｜综合评级：{level}",
            "compliance_scanner",
        ),
        _module_card(
            "核心发现",
            "以下项目优先进入法务/HR复核清单。",
            findings,
            "compliance_scanner",
            "risk",
        ),
        _module_card(
            "整改动作",
            "建议按制度、流程、留痕三条线并行整改。",
            actions,
            "compliance_scanner",
            "action",
        ),
    ]
    if attachments:
        blocks.append(
            _module_card(
                "已接收材料",
                "以下附件名称已纳入本轮审核上下文。",
                attachments,
                "compliance_scanner",
                "attachment",
            )
        )
    return DirectModuleServiceResult(active_agent="EmployerModuleAgent", askmore="no", blocks=blocks)


def _run_contract_templates(user_input: str, attachments_meta: Any | None) -> DirectModuleServiceResult:
    text = user_input.lower()
    employee_type = "标准劳动合同"
    if any(token in text for token in ["劳务", "退休返聘", "顾问"]):
        employee_type = "劳务协议"
    elif any(token in text for token in ["实习", "应届生"]):
        employee_type = "实习协议"
    elif any(token in text for token in ["派遣", "外包"]):
        employee_type = "劳务派遣/外包协议"

    clauses = [
        "明确岗位、工作地点、工时制度、报酬支付日。",
        "约定录用条件、试用期规则及不合格认定材料来源。",
        "设置保密、数据安全、设备返还和交接条款。",
    ]
    if any(token in text for token in ["竞业", "保密", "知识产权", "发明"]):
        clauses.append("补充竞业限制、知识产权归属、保密违约责任条款。")
    if employee_type == "劳务协议":
        clauses = [
            "避免出现考勤管理、单方处分等典型劳动关系表述。",
            "明确服务成果、费用结算、税费承担和解除条件。",
            "增加独立主体声明与非劳动关系声明。",
        ]
    elif employee_type == "实习协议":
        clauses = [
            "明确实习目的、期限、带教人和补贴标准。",
            "补充实习安全、商业秘密和知识成果条款。",
            "避免使用标准劳动合同中的劳动报酬、社保缴纳措辞。",
        ]

    deliverables = [
        f"推荐模板：{employee_type}",
        "输出基础条款版 + 特殊条款增补版。",
        "保留签收页、岗位说明书、员工手册确认页作为配套附件。",
    ]
    attachments = _attachment_names(attachments_meta)
    blocks = [
        _module_header(
            "合同模板生成建议",
            f"已根据输入内容匹配：{employee_type}",
            "contract_templates",
        ),
        _module_card(
            "模板条款要点",
            "以下条款建议作为本次模板的必备项。",
            clauses,
            "contract_templates",
            "clause",
        ),
        _module_card(
            "交付清单",
            "建议和主文模板同步交付以下配套文件。",
            deliverables,
            "contract_templates",
            "deliverable",
        ),
    ]
    if attachments:
        blocks.append(
            _module_card(
                "已接收材料",
                "以下附件名称已纳入模板定制参考。",
                attachments,
                "contract_templates",
                "attachment",
            )
        )
    return DirectModuleServiceResult(active_agent="EmployerModuleAgent", askmore="no", blocks=blocks)


def _run_communication_guide(user_input: str, attachments_meta: Any | None) -> DirectModuleServiceResult:
    text = user_input.lower()
    scenario = "日常管理沟通"
    if any(token in text for token in ["解除", "离职", "辞退", "协商"]):
        scenario = "协商解除沟通"
    elif any(token in text for token in ["绩效", "pip", "考核"]):
        scenario = "绩效改进沟通"
    elif any(token in text for token in ["处分", "违纪", "警告"]):
        scenario = "纪律处分沟通"

    if scenario == "协商解除沟通":
        steps = [
            "先确认事实背景和业务原因，再进入方案表达。",
            "用‘依法处理、充分沟通、给你考虑时间’替代压迫式措辞。",
            "所有补偿、交接、离职日期均以书面方案为准。",
        ]
        banned = [
            "不要说‘不签就马上走人’。",
            "不要口头承诺最终金额或额外待遇。",
            "不要在没有书面方案时要求当场签字。",
        ]
    elif scenario == "绩效改进沟通":
        steps = [
            "围绕客观指标、样本事实、改进期限展开。",
            "同步给出资源支持、辅导人和复盘节点。",
            "会后发送纪要邮件并要求确认收到。",
        ]
        banned = [
            "不要直接作出人格评价。",
            "不要使用‘能力不行’等笼统表述。",
            "不要跳过改进期直接作终局处理。",
        ]
    else:
        steps = [
            "先陈述客观事实，再说明制度依据和下一步安排。",
            "确保谈话现场至少两名公司代表在场。",
            "会后立即形成纪要并固化证据。",
        ]
        banned = [
            "不要即兴发挥与制度不一致的说法。",
            "不要承诺无法兑现的处理结果。",
            "不要遗漏员工申辩和反馈记录。",
        ]

    attachments = _attachment_names(attachments_meta)
    blocks = [
        _module_header(
            "沟通话术指南",
            f"场景识别：{scenario}｜建议采用‘事实—依据—方案—留痕’结构。",
            "communication_guide",
        ),
        _module_card(
            "推荐表达顺序",
            "以下步骤可直接作为沟通脚本骨架。",
            steps,
            "communication_guide",
            "script",
        ),
        _module_card(
            "高风险禁语",
            "以下表达容易在录音或纪要中形成不利证据，应避免。",
            banned,
            "communication_guide",
            "warning",
        ),
    ]
    if attachments:
        blocks.append(
            _module_card(
                "已接收材料",
                "以下附件名称已纳入本轮话术准备参考。",
                attachments,
                "communication_guide",
                "attachment",
            )
        )
    return DirectModuleServiceResult(active_agent="EmployerModuleAgent", askmore="no", blocks=blocks)
