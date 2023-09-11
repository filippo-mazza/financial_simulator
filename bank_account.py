class InsufficientFundsException(Exception):
    pass

class NoSubaccountException(Exception):
    pass

class BankAccount:
    def __init__(self, initial_balance=0.0, yearly_fee=0.0):
        self.subaccounts = {"Main": initial_balance}
        self.yearly_fee = yearly_fee

    def deposit(self, amount, account_name="Main"):
        if account_name not in self.subaccounts:
            raise NoSubaccountException(f"No subaccount named {account_name} found!")
        self.subaccounts[account_name] += amount

    def withdraw(self, amount, account_name="Main"):
        if account_name not in self.subaccounts:
            raise NoSubaccountException(f"No subaccount named {account_name} found!")
        
        # Check if the balance is None and set it to 0 if it is
        balance = self.subaccounts.get(account_name, 0) or 0.0
        
        if balance < amount:
            raise InsufficientFundsException("Insufficient funds!")
        
        self.subaccounts[account_name] -= amount

    def add_subaccount(self, account_name, initial_balance=0.0):
        if account_name in self.subaccounts:
            raise ValueError(f"Subaccount named {account_name} already exists!")
        self.subaccounts[account_name] = initial_balance

    def remove_subaccount(self, account_name):
        if account_name not in self.subaccounts:
            raise NoSubaccountException(f"No subaccount named {account_name} found!")
        if self.subaccounts[account_name] > 0.0:
            raise ValueError("Cannot remove a subaccount with a positive balance!")
        del self.subaccounts[account_name]

    def total_balance(self):
        return sum(self.subaccounts.values())

    def apply_yearly_fees(self):
        for account_name in self.subaccounts:
            self.subaccounts[account_name] -= self.yearly_fee
