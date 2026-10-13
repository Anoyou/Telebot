// 最小 ESLint 配置：只守 React Hooks 两条规则，不引入大范围风格规则。
// - react-hooks/rules-of-hooks: error（hook 顺序错误会直接导致运行时崩溃）
// - react-hooks/exhaustive-deps: warn（存量告警逐批清理，不阻塞提交）
// typescript-eslint 只用于解析 .ts/.tsx；不启用它的 recommended 规则集。
import tseslint from "typescript-eslint";
import reactHooks from "eslint-plugin-react-hooks";
import globals from "globals";

export default tseslint.config(
  {
    ignores: ["dist/**", "node_modules/**", "src/api/schema.ts"],
  },
  {
    files: ["src/**/*.{ts,tsx}"],
    languageOptions: {
      parser: tseslint.parser,
      ecmaVersion: 2022,
      sourceType: "module",
      globals: { ...globals.browser, ...globals.node },
    },
    plugins: { "react-hooks": reactHooks },
    rules: {
      "react-hooks/rules-of-hooks": "error",
      "react-hooks/exhaustive-deps": "warn",
    },
  },
);
