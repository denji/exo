from collections import ChainMap
from .LoopIR import LoopIR

from .memory import DRAM


# --------------------------------------------------------------------------- #
# --------------------------------------------------------------------------- #
# Memory Analysis Pass


class MemoryAnalysis:
    def __init__(self, proc):
        assert isinstance(proc, LoopIR.proc)

        self.mem_env = ChainMap()
        self.tofree = []

        self.proc = proc

        for a in proc.args:
            if a.type.is_numeric():
                mem = a.mem if a.mem else DRAM
                self.mem_env[a.name] = mem

    def result(self):
        self.push()
        body = self.mem_stmts(self.proc.body)
        self.pop()
        assert len(self.tofree) == 0

        return LoopIR.proc(
            self.proc.name,
            self.proc.args,
            self.proc.preds,
            body,
            self.proc.instr,
            self.proc.eff,
            self.proc.srcinfo,
        )

    def push(self):
        self.mem_env = self.mem_env.new_child()
        self.tofree.append([])

    def pop(self):
        self.mem_env = self.mem_env.parents
        assert len(self.tofree[-1]) == 0
        self.tofree.pop()

    def add_malloc(self, sym, typ, mem):
        assert isinstance(self.tofree[-1], list)
        assert isinstance((sym, typ, mem), tuple)
        self.tofree[-1].append((sym, typ, mem))

    def mem_stmts(self, stmts):
        if len(stmts) == 0:
            return stmts

        def used_e(e):
            res = []
            if isinstance(e, LoopIR.Read):
                res += [e.name]
                for ei in e.idx:
                    res += used_e(ei)
            elif isinstance(e, LoopIR.USub):
                res += used_e(e.arg)
            elif isinstance(e, LoopIR.BinOp):
                res += used_e(e.lhs)
                res += used_e(e.rhs)
            elif isinstance(e, LoopIR.BuiltIn):
                for ei in e.args:
                    res += used_e(ei)
            elif isinstance(e, (LoopIR.WindowExpr, LoopIR.StrideExpr)):
                res += [e.name]
            return res

        def used_s(s):
            res = []
            if isinstance(s, (LoopIR.Assign, LoopIR.Reduce)):
                res += [s.name]
                res += used_e(s.rhs)
            elif isinstance(s, LoopIR.WriteConfig):
                res += used_e(s.rhs)
            elif isinstance(s, LoopIR.If):
                res += used_e(s.cond)
                for b in s.body:
                    res += used_s(b)
                for b in s.orelse:
                    res += used_s(b)
            elif isinstance(s, LoopIR.Seq):
                for b in s.body:
                    res += used_s(b)
            elif isinstance(s, LoopIR.Alloc):
                res += [s.name]
            elif isinstance(s, LoopIR.Call):
                for e in s.args:
                    res += used_e(e)
            elif isinstance(s, LoopIR.WindowStmt):
                res += used_e(s.rhs)
            return res

        body = []
        for b in reversed([self.mem_s(b) for b in stmts]):
            used = used_s(b)
            rm = []
            for (nm, typ, mem) in self.tofree[-1]:
                if nm in used:
                    rm += [(nm, typ, mem)]
            for (nm, typ, mem) in rm:
                body += [LoopIR.Free(nm, typ, mem, None, b.srcinfo)]
                self.tofree[-1].remove((nm, typ, mem))
            body += [b]

        return list(reversed(body))

    def get_e_mem(self, e):
        if isinstance(e, (LoopIR.WindowExpr, LoopIR.Read)):
            return self.mem_env[e.name]
        else:
            assert False

    def mem_s(self, s):
        styp = type(s)

        if (
            styp is LoopIR.Pass
            or styp is LoopIR.Assign
            or styp is LoopIR.Reduce
            or styp is LoopIR.WriteConfig
        ):
            return s

        elif styp is LoopIR.WindowStmt:
            mem = self.get_e_mem(s.rhs)
            self.mem_env[s.lhs] = mem
            return s

        elif styp is LoopIR.Call:
            # check memory consistency at call boundaries
            for ca, sa in zip(s.args, s.f.args):
                if sa.type.is_numeric():
                    smem = sa.mem if sa.mem else DRAM
                    cmem = self.get_e_mem(ca)
                    if not issubclass(cmem, smem):
                        raise TypeError(
                            f"{ca.srcinfo}: expected "
                            f"argument in {smem.name()} but got an "
                            f"argument in {cmem.name()}"
                        )

            return s

        elif styp is LoopIR.If:
            self.push()
            body = self.mem_stmts(s.body)
            self.pop()
            self.push()
            ebody = self.mem_stmts(s.orelse)
            self.pop()
            return LoopIR.If(s.cond, body, ebody, None, s.srcinfo)
        elif styp is LoopIR.Seq:
            self.push()
            body = self.mem_stmts(s.body)
            self.pop()
            return styp(s.iter, s.hi, body, None, s.srcinfo)
        elif styp is LoopIR.Alloc:
            mem = s.mem if s.mem else DRAM
            self.mem_env[s.name] = mem
            self.add_malloc(s.name, s.type, s.mem)
            return s
        elif styp is LoopIR.Free:
            assert False, "There should not be frees inserted before mem " "analysis"
        else:
            assert False, f"bad case {styp}"
