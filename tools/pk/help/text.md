pk text get LABEL -> "[box.line] text"; !NNNpx marks an overflowing line
pk text ls MAP|file.inc [--grep word] | pk text grep REGEX
pk text set LABEL "plain text" [--keep-breaks] | pk text add MAP NEW_LABEL "plain text"
Plain text is wrapped by pixel width (FONT_NORMAL, 208px, 2 lines per box; vanilla style \n then \l).
|| = new box (\p), | = forced new line. Placeholders: {PLAYER} {RIVAL} {STR_VAR_1} {COLOR BLUE} ...
Not allowed in plain text: backslash codes, $, straight double quotes (use curly quotes).
--keep-breaks: you give \n \l \p yourself; widths are still checked.
pk text replace REGEX REPL [--scope MAP|file|all] (rewraps changed texts; run --dry-run first)
pk text check [MAP] [--estimates] (overflows; ~ marks lines measured with assumed placeholder widths)
pk text calibrate (re-measures vanilla, writes tools/pk/data/text_limits.json)
