# SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

"""Errors raised by metric-validity experiment tooling."""


class ExperimentError(ValueError):
    """A user-facing input, configuration, or execution error."""


class ConstantInputError(ExperimentError):
    """A correlation is undefined because an input has no variation."""
