"""Bounded Java source layout/class transport. Production use is container-only.

This is a filename/entry convention, not a Java parser or proof of compilation.
javac --release 17 -proc:none is authoritative. Unsupported layout yields a
normal compilation diagnostic, never a host fallback. No package/multi-file API.
"""
import base64
import json
from pathlib import Path
import re

ARTIFACT_BYTES = 1048576
IDENTIFIER = re.compile(r'[A-Za-z_$][A-Za-z0-9_$]{0,119}\Z')
CLASS_FILE = re.compile(r'[A-Za-z_$][A-Za-z0-9_$]{0,179}\.class\Z')
LITERALS = re.compile(r'"""[\s\S]*?"""|"(?:\\[\s\S]|[^"\\])*"|\'(?:\\[\s\S]|[^\'\\])*\'|//[^\r\n]*|/\*[\s\S]*?\*/')


def java_layout(source):
    # Unicode escapes affect Java lexical structure before comments are removed;
    # refusing them is safer than guessing the filename from a different grammar.
    if '\\u' in source:
        raise ValueError('Java 单文件模式暂不支持 Unicode 转义；可直接输入 UTF-8 文字。')
    text = LITERALS.sub(' ', source)
    tokens = re.findall(r'[\w$]+|[^\s]', text)
    depth, segment, declared = 0, [], []
    for i, token in enumerate(tokens):
        if token == 'package' and depth == 0:
            raise ValueError('Java 单文件模式不支持 package；请移除包声明。')
        if depth == 0 and token in ('class', 'record', 'enum'):
            name = tokens[i+1] if i+1 < len(tokens) else ''
            if not IDENTIFIER.fullmatch(name):
                raise ValueError('入口类名须为长度不超过 120 的 ASCII Java 标识符。')
            declared.append((name, 'public' in segment))
        if token == '{': depth += 1
        elif token == '}': depth = max(0, depth-1)
        if depth == 0:
            if token in (';', '}'): segment = []
            else: segment.append(token)
    public = [name for name, visible in declared if visible]
    names = [name for name, _ in declared]
    if not names or len(public) > 1:
        raise ValueError('请提供一个可执行顶层类；单个文件至多一个 public 顶层类型。')
    entry = 'Main' if 'Main' in names else public[0] if public else names[0] if len(names)==1 else None
    if not entry:
        raise ValueError('多个顶层类的入口不明确；请将入口命名为 Main 或声明唯一 public 类。')
    return (public[0] if public else entry) + '.java', entry


def decode_classes(raw):
    if not isinstance(raw, bytes) or len(raw) > ARTIFACT_BYTES:
        raise ValueError('Java artifact exceeds limit')
    value = json.loads(raw)
    entry, files = value.get('entry'), value.get('classes')
    if not isinstance(entry, str) or not IDENTIFIER.fullmatch(entry) or not isinstance(files, dict) or not 1 <= len(files) <= 128:
        raise ValueError('invalid Java artifact metadata')
    result, total = {}, 0
    for name, encoded in files.items():
        if not isinstance(name, str) or not CLASS_FILE.fullmatch(name) or not isinstance(encoded, str):
            raise ValueError('invalid class filename')
        data = base64.b64decode(encoded, validate=True)
        total += len(data)
        if not data.startswith(b'\xca\xfe\xba\xbe') or total > ARTIFACT_BYTES:
            raise ValueError('invalid class data')
        result[name] = data
    if entry+'.class' not in result:
        raise ValueError('entry class is missing')
    return entry, result


def encode_classes(folder, entry):
    files = {p.name:base64.b64encode(p.read_bytes()).decode('ascii') for p in sorted(Path(folder).iterdir()) if p.is_file()}
    raw = json.dumps({'entry':entry, 'classes':files}, separators=(',', ':')).encode('utf8')
    decode_classes(raw)
    return raw
