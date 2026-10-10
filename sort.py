# names = ["Zoya", "Abid", "Sadiya", "Ali"]

# names.sort()

# print(names)

# numbers = [5, 2, 8]

# numbers.sort()

# print(numbers)

# try:
#     num= int(input("Please eneter number:"))
#     print(100/num)

# except ZeroDivisionError:
#     print("cannt zreo")

# except ValueError:
#     print("value valid")

# finally:
#     print("ok")

# numbers = [2,5,10,20]
# for n in numbers:
#     value  = n * 2
#     print(value)

# names = ["abid", "ali", "abid", "soyal", "abid"]

# search = input("Enter name: ")

# count = names.count(search)

# print(f"{search} appears {count} times")

# import copy
# ori= [[1,2,3],[3,4,5,6]]
# copy = copy.deepcopy(ori)
# copy[1][2]= 99

# print(ori)
# print(copy)

# numbers = [1, 2,2,4, 3, 4, 5]
# doubled = {n: n * 2 for n in numbers}
# print(doubled)

numbers = [3, 1, 2]

sorted_numbers = sorted(numbers)
print(sorted_numbers)  # [1, 2, 3]; numbers is unchanged
print(numbers)  # [3, 1, 2]

numbers.sort()
print(numbers)  # [1, 2, 3]; numbers is sorted in place

names = ["Ali", "Abid", "Ali", "Sadiya", "Ali"]
ali_count = names.count("Ali")
print(f"'Ali' appears {ali_count} times")  # 'Ali' appears 3 times

# Example using Python's standard-library math module
import math

print(math.sqrt(81))  # 9.0

# Example using the pandas library (pip install pandas)
import pandas as pd

# data = {"Name": ["Abid", "Ali", "Sadiya", "Zoya"], "Age": [25, 30, 22, 28]}
# df = pd.DataFrame(data)


data = {"Name":["Abid","Ali","tanwar"], "Age": [10,20,30]}
df = pd.DataFrame(data)
print(df)

#      Name  Age
# 0    Abid   25
# 1     Ali   30
# 2  Sadiya   22
# 3    Zoya   28

# print(df["Age"].mean())  # 26.25

# print(df[df["Age"] > 25])
#    Name  Age
# 1   Ali   30
# 3  Zoya   28

# df["Age"] = df["Age"] + 1
# print(df.sort_values("Age"))


#      Name  Age
# 2  Sadiya   23
# 0    Abid   26
# 3    Zoya   29
# 1     Ali   31

list= [1,2,3,6,7]
total = 0
for n in list:
    if n < 7:
        total += n
    else:
        total += n * 2
        print(total)


    
