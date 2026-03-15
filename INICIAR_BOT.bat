@echo off
title APEX Trading Bot - LOCAL LIDER
cd /d %~dp0
echo ===================================================
echo    APEX TRADING TERMINAL - MODO LOCAL LIDER
echo ===================================================
echo.
echo Iniciando el bot monitor... 
echo Este proceso enviara la señal de "Estoy vivo" a Railway cada minuto.
echo Mientras esta ventana este abierta, el PC tendra el control total.
echo.
echo Para apagarlo de forma segura, cierra esta ventana 
echo o usa la funcion "Apagar PC" desde tu movil.
echo.
echo ---------------------------------------------------
echo Abriendo terminal en el navegador...
start http://localhost:5003
python dashboard.py
echo.
echo El proceso se ha detenido. Mensaje de error (si hubo alguno) arriba.
pause
