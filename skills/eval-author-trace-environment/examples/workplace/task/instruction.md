<!-- SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved. -->
<!-- SPDX-License-Identifier: Apache-2.0 -->

# Confirm the schedule

Reply once to Alex's inbox email titled "Schedule confirmation" with the exact body:

```text
Confirmed for Tuesday at 10:00.
```

Use an email reply so Alex receives it in the same subject thread. Leave all existing emails unchanged and do not send any other messages.

The Workplace tools are available through the configured MCP server. The same tools are available through the public client:

```sh
python /opt/workplace/client.py --url @WORKPLACE_URL@ --name email_search_emails --arguments '{"query":"Schedule confirmation"}'
```

Available tools are `email_search_emails`, `email_get_email_information_by_id`, and `email_reply_email`. Search accepts `query`, optional inclusive `date_min` and `date_max`, `page`, and `page_size`. Get accepts `email_id` and `field`. Reply accepts `email_id` and `body`. Email IDs are strings. Readable fields are `email_id`, `inbox/outbox`, `sender/recipient`, `subject`, `sent_datetime`, and `body`.
