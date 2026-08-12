"""应用编排服务层（批次2：管线收敛）。

routers / tools / jobs 只允许 import 本层，不再直接编排 domain 步骤。
每个 service 是薄封装：透传 domain runner 的签名，不改行为。
"""
