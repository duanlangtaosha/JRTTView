#! python2
#coding: utf-8
import os
import sys
import ctypes
import struct
import ConfigParser

import sip
sip.setapi('QString', 2)
from PyQt4 import QtCore, QtGui, uic
from PyQt4.Qwt5 import QwtPlot, QwtPlotCurve


class RingBuffer(ctypes.Structure):
    _fields_ = [
        ('sName',        ctypes.POINTER(ctypes.c_char)),
        ('pBuffer',      ctypes.POINTER(ctypes.c_byte)),
        ('SizeOfBuffer', ctypes.c_uint),
        ('WrOff',        ctypes.c_uint),    # Position of next item to be written. 对于aUp：   芯片更新WrOff，主机更新RdOff
        ('RdOff',        ctypes.c_uint),    # Position of next item to be read.    对于aDown： 主机更新WrOff，芯片更新RdOff
        ('Flags',        ctypes.c_uint),
    ]

class SEGGER_RTT_CB(ctypes.Structure):      # Control Block
    _fields_ = [
        ('acID',              ctypes.c_char * 16),
        ('MaxNumUpBuffers',   ctypes.c_uint),
        ('MaxNumDownBuffers', ctypes.c_uint),
        ('aUp',               RingBuffer * 2),
        ('aDown',             RingBuffer * 2),
    ]


'''
from RTTView_UI import Ui_RTTView
class RTTView(QtGui.QWidget, Ui_RTTView):
    def __init__(self, parent=None):
        super(RTTView, self).__init__(parent)
        
        self.setupUi(self)
'''
class RTTView(QtGui.QWidget):
    def __init__(self, parent=None):
        super(RTTView, self).__init__(parent)
        
        uic.loadUi('RTTView.ui', self)

        self.initSetting()

        self.initQwtPlot()

        self.rcvbuff = b''
        
        self.tmrRTT = QtCore.QTimer()
        self.tmrRTT.setInterval(10)
        self.tmrRTT.timeout.connect(self.on_tmrRTT_timeout)
        self.tmrRTT.start()
    
    def initSetting(self):
        if not os.path.exists('setting.ini'):
            open('setting.ini', 'w')
        
        self.conf = ConfigParser.ConfigParser()
        self.conf.read('setting.ini')
        
        if not self.conf.has_section('J-Link'):
            self.conf.add_section('J-Link')
            self.conf.set('J-Link', 'dllpath', '')
            self.conf.add_section('Memory')
            self.conf.set('Memory', 'StartAddr', '0x20000000')

        self.linDLL.setText(self.conf.get('J-Link', 'dllpath').decode('gbk'))

    def initQwtPlot(self):
        self.PlotData = [0]*1000
        
        self.qwtPlot = QwtPlot(self)
        self.qwtPlot.setVisible(False)
        self.vLayout.insertWidget(0, self.qwtPlot)
        
        self.PlotCurve = QwtPlotCurve()
        self.PlotCurve.attach(self.qwtPlot)
        self.PlotCurve.setData(range(1, len(self.PlotData)+1), self.PlotData)
    
    @QtCore.pyqtSlot()
    def on_btnOpen_clicked(self):
        if self.btnOpen.text() == u'打开连接':
            try:
                self.jlink = ctypes.cdll.LoadLibrary(self.linDLL.text())

                err_buf = (ctypes.c_char * 64)()
                self.jlink.JLINKARM_ExecCommand('Device = Cortex-M0', err_buf, 64)

                self.jlink.JLINKARM_TIF_Select(1)
                self.jlink.JLINKARM_SetSpeed(8000)
                
                buff = ctypes.create_string_buffer(1024)
                Addr = int(self.conf.get('Memory', 'StartAddr'), 16)
                for i in range(256):
                    self.jlink.JLINKARM_ReadMem(Addr + 1024*i, 1024, buff)
                    index = buff.raw.find('SEGGER RTT')
                    if index != -1:
                        self.RTTAddr = Addr + 1024*i + index

                        buff = ctypes.create_string_buffer(ctypes.sizeof(SEGGER_RTT_CB))
                        self.jlink.JLINKARM_ReadMem(self.RTTAddr, ctypes.sizeof(SEGGER_RTT_CB), buff)

                        rtt_cb = SEGGER_RTT_CB.from_buffer(buff)
                        self.aUpAddr = self.RTTAddr + 16 + 4 + 4
                        self.aDownAddr = self.aUpAddr + ctypes.sizeof(RingBuffer) * rtt_cb.MaxNumUpBuffers

                        self.txtMain.append('\n_SEGGER_RTT @ 0x%08X with %d aUp and %d aDown\n' %(self.RTTAddr, rtt_cb.MaxNumUpBuffers, rtt_cb.MaxNumDownBuffers))
                        break
                else:
                    raise Exception('Can not find _SEGGER_RTT')
            except Exception as e:
                self.txtMain.append('\n%s\n' %str(e))
            else:
                self.linDLL.setEnabled(False)
                self.btnDLL.setEnabled(False)
                self.btnOpen.setText(u'关闭连接')
        else:
            self.linDLL.setEnabled(True)
            self.btnDLL.setEnabled(True)
            self.btnOpen.setText(u'打开连接')
    
    def aUpRead(self):
        buf = ctypes.create_string_buffer(ctypes.sizeof(RingBuffer))
        self.jlink.JLINKARM_ReadMem(self.aUpAddr, ctypes.sizeof(RingBuffer), buf)

        aUp = RingBuffer.from_buffer(buf)
        
        if aUp.RdOff == aUp.WrOff:
            str = ctypes.create_string_buffer(0)

        elif aUp.RdOff < aUp.WrOff:
            cnt = aUp.WrOff - aUp.RdOff
            str = ctypes.create_string_buffer(cnt)
            self.jlink.JLINKARM_ReadMem(ctypes.cast(aUp.pBuffer, ctypes.c_void_p).value + aUp.RdOff, cnt, str)
            
            aUp.RdOff += cnt
            
            self.jlink.JLINKARM_WriteU32(self.aUpAddr + 4*4, aUp.RdOff)

        else:
            cnt = aUp.SizeOfBuffer - aUp.RdOff
            str = ctypes.create_string_buffer(cnt)
            self.jlink.JLINKARM_ReadMem(ctypes.cast(aUp.pBuffer, ctypes.c_void_p).value + aUp.RdOff, cnt, str)
            
            aUp.RdOff = 0  #这样下次再读就会进入执行上个条件
            
            self.jlink.JLINKARM_WriteU32(self.aUpAddr + 4*4, aUp.RdOff)
        
        return str.raw
    
    def on_tmrRTT_timeout(self):
        if self.btnOpen.text() == u'关闭连接':
            try:
                self.rcvbuff += self.aUpRead()

                if self.txtMain.isVisible():
                    if self.chkHEXShow.isChecked():
                        text = ''.join('%02X ' %ord(c) for c in self.rcvbuff)

                    else:
                        text = self.rcvbuff

                    if len(self.txtMain.toPlainText()) > 25000: self.txtMain.clear()
                    self.txtMain.moveCursor(QtGui.QTextCursor.End)
                    self.txtMain.insertPlainText(text)

                    self.rcvbuff = b''

                else:
                    if self.rcvbuff.rfind(',') == -1: return
                    
                    d = [int(x) for x in self.rcvbuff[0:self.rcvbuff.rfind(',')].split(',')]
                    for x in d:
                        self.PlotData.pop(0)
                        self.PlotData.append(x)
                    self.PlotCurve.setData(range(1, len(self.PlotData)+1), self.PlotData)
                    self.qwtPlot.replot()

                    self.rcvbuff = self.rcvbuff[self.rcvbuff.rfind(',')+1:]

            except Exception as e:
                self.rcvbuff = b''
                self.txtMain.append('\n%s\n' %str(e))
    
    def aDownWrite(self, bytes):
        buf = ctypes.create_string_buffer(ctypes.sizeof(RingBuffer))
        self.jlink.JLINKARM_ReadMem(self.aDownAddr, ctypes.sizeof(RingBuffer), buf)

        aDown = RingBuffer.from_buffer(buf)
        
        if aDown.WrOff >= aDown.RdOff:
            if aDown.RdOff != 0: cnt = min(aDown.SizeOfBuffer - aDown.WrOff, len(bytes))
            else:                cnt = min(aDown.SizeOfBuffer - 1 - aDown.WrOff, len(bytes))    # 写入操作不能使得 aDown.WrOff == aDown.RdOff，以区分满和空
            str = ctypes.create_string_buffer(bytes[:cnt])
            self.jlink.JLINKARM_WriteMem(ctypes.cast(aDown.pBuffer, ctypes.c_void_p).value + aDown.WrOff, cnt, str)
            
            aDown.WrOff += cnt
            if aDown.WrOff == aDown.SizeOfBuffer: aDown.WrOff = 0

            bytes = bytes[cnt:]

        if bytes and aDown.RdOff != 0 and aDown.RdOff != 1:     # != 0 确保 aDown.WrOff 折返回 0，!= 1 确保有空间可写入
            cnt = min(aDown.RdOff - 1 - aDown.WrOff, len(bytes))    # - 1 确保写入操作不导致WrOff与RdOff指向同一位置
            str = ctypes.create_string_buffer(bytes[:cnt])
            self.jlink.JLINKARM_WriteMem(ctypes.cast(aDown.pBuffer, ctypes.c_void_p).value + aDown.WrOff, cnt, str)

            aDown.WrOff += cnt

        self.jlink.JLINKARM_WriteU32(self.aDownAddr + 4*3, aDown.WrOff)

    @QtCore.pyqtSlot()
    def on_btnSend_clicked(self):
        if self.btnOpen.text() == u'关闭连接':
            text = self.txtSend.toPlainText()

            try:
                if self.chkHEXSend.isChecked():
                    bytes = ''.join([chr(int(x, 16)) for x in text.split()])

                else:
                    bytes = text

                self.aDownWrite(bytes)

            except Exception as e:
                self.txtMain.append('\n%s\n' %str(e))

    @QtCore.pyqtSlot()
    def on_btnDLL_clicked(self):
        path = QtGui.QFileDialog.getOpenFileName(caption=u'JLinkARM.dll路径', filter=u'动态链接库文件 (*.dll)', directory=self.linDLL.text())
        if path != '':
            self.linDLL.setText(path)

    @QtCore.pyqtSlot(int)
    def on_chkWavShow_stateChanged(self, state):
        self.qwtPlot.setVisible(state == QtCore.Qt.Checked)
        self.txtMain.setVisible(state == QtCore.Qt.Unchecked)

    @QtCore.pyqtSlot()
    def on_btnClear_clicked(self):
        self.txtMain.clear()
    
    def closeEvent(self, evt):
        self.conf.set('J-Link', 'dllpath', self.linDLL.text().encode('gbk'))
        self.conf.write(open('setting.ini', 'w'))
        

if __name__ == "__main__":
    app = QtGui.QApplication(sys.argv)
    rtt = RTTView()
    rtt.show()
    app.exec_()
