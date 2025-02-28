from __future__ import annotations

import itertools
import os
import platform

import pytest

from exo import proc
from exo.platforms.neon import *
from exo.stdlib.scheduling import *
from exo.memory import MemGenError

import numpy as np


def test_neon_can_read():
    @proc
    def read_neon(n: size, dst: R[n] @ DRAM, src: R[n] @ Neon4f):
        for i in seq(0, n):
            dst[i] = src[i]

    with pytest.raises(MemGenError, match="cannot read"):
        read_neon.c_code_str()


@pytest.mark.isa("neon")
def test_neon_memcpy(compiler):
    """
    Compute dst = src
    """

    @proc
    def memcpy_neon(n: size, dst: R[n] @ DRAM, src: R[n] @ DRAM):  # pragma: no cover
        for i in seq(0, (n + 3) / 4):
            if n - 4 * i >= 4:
                tmp: f32[4] @ Neon4f
                neon_vld_4xf32(tmp, src[4 * i : 4 * i + 4])
                neon_vst_4xf32(dst[4 * i : 4 * i + 4], tmp)
            else:
                for j in seq(0, n - 4 * i):
                    dst[4 * i + j] = src[4 * i + j]

    fn = compiler.compile(
        memcpy_neon, skip_on_fail=True, CMAKE_C_FLAGS="-mcpu=apple-a14"
    )

    for n in (7, 8, 9, 31, 32, 33, 127, 128, 129):
        inp = np.array([float(i) for i in range(n)], dtype=np.float32)
        out = np.array([float(0) for _ in range(n)], dtype=np.float32)
        fn(None, n, out, inp)

        assert np.array_equal(inp, out)


@pytest.mark.isa("neon")
def test_neon_simple_math(compiler):
    """
    Compute x = x * y^2
    """

    @proc
    def simple_math_neon(n: size, x: R[n] @ DRAM, y: R[n] @ DRAM):  # pragma: no cover
        assert n % 4 == 0
        for i in seq(0, n / 4):
            xVec: f32[4] @ Neon4f
            yVec: f32[4] @ Neon4f
            neon_vld_4xf32(xVec, x[4 * i : 4 * i + 4])
            neon_vld_4xf32(yVec, y[4 * i : 4 * i + 4])
            neon_vmul_4xf32(xVec, xVec, yVec)
            neon_vmul_4xf32(xVec, xVec, yVec)
            neon_vst_4xf32(x[4 * i : 4 * i + 4], xVec)

    fn = compiler.compile(
        simple_math_neon, skip_on_fail=True, CMAKE_C_FLAGS="-mcpu=apple-a14"
    )

    for n in (4, 8, 12, 16, 24, 32, 64, 128):
        x = np.array([float(i) for i in range(n)], dtype=np.float32)
        y = np.array([float(3 * i) for i in range(n)], dtype=np.float32)
        expected = x * y * y

        fn(None, n, x, y)
        assert np.allclose(x, expected)


@pytest.fixture
def simple_math_neon_sched():
    @proc
    def simple_math_neon_sched(
        n: size, x: R[n] @ DRAM, y: R[n] @ DRAM
    ):  # pragma: no cover
        for i in seq(0, n):
            x[i] = x[i] * y[i] * y[i]

    def sched_neon(p=simple_math_neon_sched):
        p = divide_loop(p, "i", 4, ["io", "ii"], tail="cut_and_guard")
        p = stage_assn(p, "x[_] = _ #0", "xyy")
        p = autolift_alloc(p, "xyy: _", keep_dims=True)
        p = fission(p, p.find("xyy[_] = _").after())

        p = bind_expr(p, "x[_]", "xVec")
        p = autolift_alloc(p, "xVec: _", keep_dims=True)
        p = fission(p, p.find("xVec[_] = _").after())

        p = bind_expr(p, "y[_]", "yVec", cse=True)
        p = autolift_alloc(p, "yVec: _", keep_dims=True)
        p = fission(p, p.find("yVec[_] = _").after())

        p = bind_expr(p, "xVec[_] * yVec[_]", "xy")
        p = autolift_alloc(p, "xy: _", keep_dims=True)
        p = fission(p, p.find("xy[_] = _").after())

        p = set_memory(p, "xVec", Neon4f)
        p = set_memory(p, "yVec", Neon4f)
        p = set_memory(p, "xy", Neon4f)
        p = set_memory(p, "xyy", Neon4f)
        p = replace(p, "for ii in _: _ #4", neon_vst_4xf32)
        p = replace_all(p, neon_vld_4xf32)
        p = replace_all(p, neon_vmul_4xf32)

        return p

    simple_math_neon_sched = sched_neon()

    return simple_math_neon_sched


def test_gen_neon_simple_math_scheduling(golden, simple_math_neon_sched):
    assert str(simple_math_neon_sched) == golden


@pytest.mark.isa("neon")
def test_neon_simple_math_scheduling(compiler, simple_math_neon_sched):
    """
    Compute x = x * y^2
    """

    fn = compiler.compile(
        simple_math_neon_sched, skip_on_fail=True, CMAKE_C_FLAGS="-mcpu=apple-a14"
    )

    for n in (4, 8, 12, 16, 24, 32, 64, 128):
        x = np.array([float(i) for i in range(n)], dtype=np.float32)
        y = np.array([float(3 * i) for i in range(n)], dtype=np.float32)
        expected = x * y * y

        fn(None, n, x, y)
        assert np.allclose(x, expected)
