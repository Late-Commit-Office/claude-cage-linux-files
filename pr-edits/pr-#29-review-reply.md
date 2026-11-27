感谢您细致的审查。几处问题都已复现确认，并在 `0.2.6` 中修复，每项都补了测试（改动前会失败）：

1. **`//` 与软链接绕过限制**
   - `absolute()` 先把开头的 `//+` 归并为 `/` 再 `normpath`：`list_directory({"path": "//"})` 现在返回 `path: "/"`、`parent: null`。
   - 新增 `off_limits()`，先 `realpath` 再判断 `/` 和 `/proc`、`/sys`、`/dev`。`export_start` 和 `text_preview` 都改用它。导出软链时复制的是解析后的目标，文件名仍沿用软链名。
   - `write_zip` 改为用 inode（`st_dev`、`st_ino`）识别正在写入的压缩包，不再比较路径，所以换一种写法或经由软链都无法让压缩包打包自身。
2. **`openPath` 并发**：页面在 `request()` 里加了一个最多 4 路的请求队列，`openPath`、`refreshPlaces` 和导出轮询等所有请求都经过它。页面测试新增了打开并刷新 16 层深目录的用例，测试工作台按 8 路上限拒绝超额请求。
3. **`write_zip` 异常处理与 `O_NOFOLLOW`**
   - 每个文件都用 `O_RDONLY | O_NOFOLLOW | O_NONBLOCK` 打开，再 `fstat` 确认是普通文件，`ZipInfo` 也由这次 `fstat` 生成。
   - 打开阶段的任何 `OSError`（被删、换成软链、`EIO`/`ESTALE` 等）都会跳过该文件；读到一半出错同样跳过，并注明该文件在压缩包里不完整。只有写入压缩包本身出错（如磁盘已满）才会让导出失败。
   - 单文件导出也改用同样的打开方式。
4. **PR 信息**：描述已改为 `0.2.6`。
5. `shared_mounts` 现在也检查 superblock 选项里的 `ro`；临时文件改名为 `.{job_id}.partial`，并加了 250 字节文件名的导出测试。

另外自查时又修了两处会让整个压缩导出失败的问题：文件名不是合法 UTF-8 时（Linux 允许），条目名里的坏字节改为显示 `U+FFFD`；修改时间超出 zip 格式支持的 1980–2107 年范围时，按边界截断。

关于 `authors.json`：希望保留组织名 `late-commit-office`。我平时公开写代码基本都用这个组织，个人账号另作他用，想把两者分开。不知道这样是否能够接受？如果必须是个人用户名，也可以改成 `loikein`。
