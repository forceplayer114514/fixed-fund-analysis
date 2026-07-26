On fundmonitors.com, find the fund profile page for the Australian fund
named "{fund_name}".

fundmonitors.com fund URLs carry a FundID and an AccCode, for example:
  fund-factsheet.php?FundID=1512&AccCode=fresnjxju

I need the FundID and AccCode for this exact fund. Be careful: many Australian
funds have sibling share classes with near-identical names — return the one whose
name matches "{fund_name}" most exactly, and if you are not confident it is the
same fund, return null rather than guessing.

Answer with JSON only, no other text:
{"fund_id": <int or null>, "acc_code": "<string or empty>", "page_fund_name": "<name as shown on the page, or null>"}
