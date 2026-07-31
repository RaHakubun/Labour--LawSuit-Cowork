import os
import sys

ROOT = os.path.dirname(os.path.dirname(__file__))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from utils.pkulaw_mcp_client import mcp_query  # noqa: E402
from utils.pkulaw_mcp_catalog import load_catalog  # noqa: E402


BASE_URL = "<YOUR_BASE_URL>"
##
#可以调用的MCP服务
# 检索法律法规-语义
# 修正生成幻觉-法条
# 法宝超链
# 法条识别与溯源
# 案号识别与溯源
# 检索司法案例-关键词
# 检索司法案例-语义
# 精准查找法条-关键词
# 检索法律法规-关键词
# 法宝语义检索（NL-SQL）
# ##

def run_mcp_query(
    service_name="检索法律法规-语义",
    query="租房合同纠纷应该适用哪些法律条款？",
) -> str:
    token = os.getenv("PKULAW_MCP_TOKEN", "").strip()
    if not token:
        raise RuntimeError("PKULAW_MCP_TOKEN is required")
    catalog = load_catalog()
    return mcp_query(service_name, query, token=token, catalog=catalog)




if __name__ == "__main__":
    catalog = load_catalog()
    print(catalog)
    print("\ncontent:法条识别与溯源","帮我识别这段判决书中引用的法律依据：本院依照《中华人民共和国民事诉讼法》第一百七十条、《最高人民法院关于适用〈中华人民共和国民事诉讼法〉的解释》第三百三十条之规定，判决如下...")
    content = run_mcp_query("法条识别与溯源","帮我识别这段判决书中引用的法律依据：本院依照《中华人民共和国民事诉讼法》第一百七十条、《最高人民法院关于适用〈中华人民共和国民事诉讼法〉的解释》第三百三十条之规定，判决如下...")
    print(content)
    print("\ncontent2:检索司法案例-语义","帮我找到一个实习期被解雇的案例")
    content2 = run_mcp_query("检索司法案例-语义","帮我找到一个实习期被解雇的案例")
    print(content2)
    print("\ncontent3:检索司法案例-关键词","帮我找到一个实习期拿到Offer未入职就被破坏雇佣关系的案例")
    content3 = run_mcp_query("检索司法案例-关键词","帮我找到一个实习期拿到Offer未入职就被破坏雇佣关系的案例")
    print(content3)
    print("\ncontent4:检索法律法规-语义","帮我识别一个实习期拿到Offer未入职就被破坏雇佣关系的案例会涉及到的法律法规")
    content4 = run_mcp_query("检索法律法规-语义","帮我识别一个实习期拿到Offer未入职就被破坏雇佣关系的案例会涉及到的法律法规")
    print(content4)
