#!/bin/sh
# SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

set -eu
python /opt/workplace/client.py --url "${WORKPLACE_URL:-@WORKPLACE_URL@}" --name email_search_emails --arguments '{"query":"Schedule confirmation"}'
python /opt/workplace/client.py --url "${WORKPLACE_URL:-@WORKPLACE_URL@}" --name email_get_email_information_by_id --arguments '{"email_id":"1","field":"body"}'
python /opt/workplace/client.py --url "${WORKPLACE_URL:-@WORKPLACE_URL@}" --name email_reply_email --arguments '{"email_id":"1","body":"Confirmed for Tuesday at 10:00."}'
python /opt/workplace/client.py --url "${WORKPLACE_URL:-@WORKPLACE_URL@}" --name email_reply_email --arguments '{"email_id":"1","body":"Confirmed for Tuesday at 10:00."}'
