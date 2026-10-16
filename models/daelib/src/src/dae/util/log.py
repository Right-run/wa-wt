import sys
from os import path

sys.path.append(path.dirname(path.dirname(path.abspath(__file__))))

from time import time
from util.enums import *
from threading import currentThread

startTime = time()

def getTime():
	return "%08.4f" % (time() - startTime)

curLevel = 0
levelStr = ""

# 缩进上限：addLevel/subLevel 在 daelib 里是手工配对且没有 try/finally，解析异常会让层级只增不减
# （实测单包 abandoned.grp 就泄漏 99 级，一轮导出缩进涨到 4289 个空格，日志 94.9% 是空白字符、
#   终端把一条日志折成 4 行显示）。这里把缩进夹在 [0, MAX_LEVEL]，失衡不再累积。
MAX_LEVEL = 8

def incrLevel(level:int = 0):
	global curLevel
	global levelStr

	curLevel = max(0, min(curLevel + level, MAX_LEVEL))
	levelStr = ""

	if curLevel < 0:
		raise Exception(f"Impossible level {curLevel}")

	for i in range(curLevel):
		levelStr += "    "

def addLevel(level:int = 1):
	incrLevel(level)

def subLevel(level:int = 1):
	incrLevel(-level)


def getLevelStr():
	global levelStr

	return levelStr

def log(message:str, type:int = 0): # TODO: add multithreading support
	print(f"{getTime()} [{currentThread().ident}] ", end = "")

	if type == LOG_DEBUG: # could use a switch statement here
		print("[D]", end = "")
	elif type == LOG_ERROR:
		print("[E]", end = "")
	else:
		print("[W]", end = "")
	
	print(f" | {getLevelStr()}{message}")

	pass
