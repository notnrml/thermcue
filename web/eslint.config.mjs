/**
 * Flat config, required by ESLint 9.
 *
 * ESLint 9 arrived with Next 16: eslint-config-next@16 requires eslint >= 9, and
 * eslint 9 no longer reads .eslintrc.json. eslint-config-next@16 ships native
 * flat configs, so these are imported directly rather than through the
 * FlatCompat shim — the shim fails on this config with a circular-reference
 * error while serialising its own validation output.
 *
 * The rule set is unchanged from the .eslintrc.json it replaces.
 */
import coreWebVitals from "eslint-config-next/core-web-vitals";
import typescript from "eslint-config-next/typescript";

const config = [
  ...(Array.isArray(coreWebVitals) ? coreWebVitals : [coreWebVitals]),
  ...(Array.isArray(typescript) ? typescript : [typescript]),
  {
    ignores: [".next/**", "node_modules/**", "out/**", "next-env.d.ts"],
  },
];

export default config;
