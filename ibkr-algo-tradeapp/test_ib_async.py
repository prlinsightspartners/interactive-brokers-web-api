from ib_async import IB  # or use the official native ibapi

ib = IB()
ib.connect("127.0.0.1", 7497, clientId=1)  # Use port 7497 for live, 7496 for paper

# Test the connection
print(ib.managedAccounts())