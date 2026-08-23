// 为使用 vitest 全局 API（describe/it/expect/vi）的文件提供类型声明。
/// <reference types="vitest/globals" />

// 将 @testing-library/jest-dom 匹配器合并到 vitest 全局 Assertion。
// 不引用 '@testing-library/jest-dom/vitest' 入口：其运行期会 import 'vitest'，
// 与本项目 globals 模式的 runner 实例不匹配（运行期改从 /matchers 手动注册，
// 见 test-setup.ts）；此处仅补齐类型声明，写法与官方 vitest.d.ts 一致。
import { type TestingLibraryMatchers } from '@testing-library/jest-dom/matchers';

declare module 'vitest' {
  interface Assertion<T = any> extends TestingLibraryMatchers<any, T> {}
  interface AsymmetricMatchersContaining extends TestingLibraryMatchers<any, any> {}
}
