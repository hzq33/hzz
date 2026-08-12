// Commitlint — Conventional Commits 规范
// 文档: https://commitlint.js.org/
module.exports = {
  extends: ['@commitlint/config-conventional'],
  rules: {
    // 类型枚举：feat/fix/docs/style/refactor/perf/test/build/ci/chore/revert
    'type-enum': [
      2,
      'always',
      [
        'feat', // 新功能
        'fix', // Bug 修复
        'docs', // 文档变更
        'style', // 代码格式（不影响功能）
        'refactor', // 重构（既不是 feat 也不是 fix）
        'perf', // 性能优化
        'test', // 测试相关
        'build', // 构建系统或外部依赖变更
        'ci', // CI 配置
        'chore', // 杂项（不修改 src 或测试）
        'revert', // 回滚提交
      ],
    ],
    // type 不能为空、必须小写
    'type-empty': [2, 'never'],
    'type-case': [2, 'always', 'lower-case'],
    // subject 不能为空、不能以 . 结尾、不能超过 72 字符
    'subject-empty': [2, 'never'],
    'subject-full-stop': [0],
    'subject-case': [0],
    'subject-max-length': [2, 'always', 72],
    'header-max-length': [2, 'always', 100],
    // body 每行不超过 100 字符
    'body-max-line-length': [2, 'always', 100],
    // footer 不限制
    'footer-leading-blank': [1, 'always'],
  },
};
