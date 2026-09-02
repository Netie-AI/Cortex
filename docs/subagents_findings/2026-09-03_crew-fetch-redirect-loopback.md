# Crew web_fetch refuses 3xx onto loopback

Keywords: crew, research, web_fetch, redirect, ssrf, loopback, r-0011, graph, coordinate
Main idea: Crew web_fetch no longer calls engine fetch (which follows any 3xx). PublicRedirectHandler refuses hop onto loopback/RFC1918. Graph/activity name none. Control coordinate poll catch names unread.

Live `:8020` is still the Cortex-crew fork. Do not restart it (R-0015).
