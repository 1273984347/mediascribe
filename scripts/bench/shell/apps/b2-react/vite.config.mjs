import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

// B2 候选: Vite + React 构建的等价最小工作台壳, 产物由 FastAPI 静态托管。
// base: "./" —— 相对路径引用, 保证被挂在任意前缀下都能加载。
export default defineConfig({
  plugins: [react()],
  base: "./",
  build: {
    outDir: "dist",
    emptyOutDir: true,
    assetsDir: "assets",
    target: "es2020",
    reportCompressedSize: true,
  },
});
