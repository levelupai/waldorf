var socket;
var freezeUpdatesUntil = -1;
var masterVersion = undefined;
var exportTableSettings = {};
var connStatusChangeTime = Date.now();
var allSlavesBackup;
var onlineSlavesBackup;
var onlineSlavesDedupSorted;
var hideOfflineClients = true;
var hideOfflineSlaves = true;
var mostRecentHideClientTime = Date.now();
var mostRecentHideSlaveTime = Date.now();
var masterUptimeOnConnect = undefined;
var singleMachineFormVisible = true;
var singleMachineSelectedMachine = '';
var cpuTableVisible = false;
var clientsCheckboxChecked = false;
var slavesCheckboxChecked = false;
var isConfigurationLoaded = false;
var selectedPreset = undefined;

function freezeUpdates() {
  freezeUpdatesUntil = Date.now() + 15*1000;
}

function unfreezeUpdates() {
  freezeUpdatesUntil = -1;
}

function deleteConfig() {
  var ok = confirm('Are you sure you want to delete?');
  if (ok) {
    var element = document.getElementById('presetsSelect');
    var uid = element.options[element.selectedIndex].value;
    if (uid !== undefined && uid !== '') {
      var configs = JSON.parse(localStorage.getItem('presets'));
      delete configs[uid];
      localStorage.setItem('presets', JSON.stringify(configs));
    }
  }
  unfreezeUpdates();
  displaySavedPresets();
  socket.emit('get_info');
  return ok;
}

function connTimerUpdate() {
  var timerSpan = document.getElementById('masterStatusTimer');
  var currentDate = Date.now();
  var uptimeDays = Math.floor((currentDate - connStatusChangeTime) / 86400000);
  var daysText = ' DAYS ';
  if (uptimeDays == 1) daysText = ' DAY ';
  timerSpan.innerText = uptimeDays + daysText +
      new Date(currentDate - connStatusChangeTime).toISOString().substr(11, 8);
}

function toggleArrow(divName, transitionTime) {
  var arrowDiv = divName + 'Arrow';
  $('#' + divName).toggle(transitionTime);
  if ($('#' + arrowDiv)[0].innerHTML.includes('up')) {
    $('#' + arrowDiv)[0].innerHTML =
        '<img src="/static/images/arrow_down.png" />';
  } else {
    $('#' + arrowDiv)[0].innerHTML =
        '<img src="/static/images/arrow_up.png" />';
  }
}

function applyCoreChange() {
  var newCoreCount = document.getElementById('coreSelect').value;
  var element = document.getElementById('singleSelect');
  var uid = element.options[element.selectedIndex].value;
  if (uid != '') {
    socket.emit('change_core', [uid, parseInt(newCoreCount, 10)]);
  }
  unfreezeUpdates();
}

function updateExportTable(id, value) {
  exportTableSettings[id.substr(7)] = value;
  var selects = $("select[id|='export']");
  exportTableSettings = {};
  for (var select in selects) {
    var machineName = selects[select].id;
    var newValue = selects[select].value;
    if (machineName != undefined) {
      exportTableSettings[machineName.substr(7)] = newValue;
    }
  }
}

function hideOverlay() {
  document.getElementById('keyboardShortcutsGuide').style.visibility =
      'hidden';
}

function showOverlay() {
  document.getElementById('keyboardShortcutsGuide').style.visibility =
      'visible';
}

function stringifyCPUTable() {
  var modeName = document.getElementById('batchExportName').value;
  var buffer = '{\n    "config_name": "' + modeName + '",\n';
  buffer += '    "machines": {\n';
  for (var prop in exportTableSettings) {
    buffer += '        "'+prop+'": ';
    buffer += exportTableSettings[prop];
    buffer += ',';
    buffer += '\n';
  }
  buffer = buffer.substr(0, buffer.length - 2) + '\n';
  buffer += '    }\n';
  buffer += '}';
  return [modeName, buffer];
}

function saveConfigToLocalStorage() {
  var modeNameBuffer = stringifyCPUTable();
  var modeName = modeNameBuffer[0];
  var buffer = modeNameBuffer[1];
  setPresetInLocalStorage(modeName, JSON.parse(buffer).machines);
  unfreezeUpdates();
  document.getElementById('batchExportMessages').innerHTML =
      'Configuration saved to local storage';
  socket.emit('get_info');
}


function exportFile() {
  var modeNameBuffer = stringifyCPUTable();
  var modeName = modeNameBuffer[0];
  var buffer = modeNameBuffer[1];
  var download = function(content, fileName, mimeType) {
    var a = document.createElement('a');
    if (navigator.msSaveBlob) { // IE10
      navigator.msSaveBlob(new Blob([content], {
        type: mimeType
      }), fileName);
    } else if (URL && 'download' in a) { //h5 a[download]
      a.href = URL.createObjectURL(new Blob([content], {
        type: mimeType
      }));
      a.setAttribute('download', fileName);
      document.body.appendChild(a);
      a.click();
      document.body.removeChild(a);
    } else {
      location.href = 'data:application/octet-stream,' +
          encodeURIComponent(content);
    }
  }
  download(buffer, modeName + '.json', 'application/json;encoding:utf-8');
  document.getElementById('batchExportMessages').innerHTML =
      'File download ready. Please check your downloaded files.';
}


function batchApplyCpuChanges() {
  for (var i in exportTableSettings) {
    var newUsedCores = parseInt(exportTableSettings[i], 10);
    for (var j in onlineSlavesBackup) {
      if (onlineSlavesBackup[j]['Hostname'] == i) {
        var currentUsedCores = parseInt(onlineSlavesBackup[j]['USED_CORES']);
        if (currentUsedCores != newUsedCores) {
          socket.emit('change_core',
                      [
                        onlineSlavesBackup[j]['UID'],
                        parseInt(exportTableSettings[i], 10)
                      ]
          );
        }
      }
    }
  }
  document.getElementById('applyBatchSettingsMessages').innerHTML =
      'Settings applied. Please wait and check table below for updates.';
  unfreezeUpdates();
}

function updateCoreSelector() {
  var element = document.getElementById('singleSelect');
  var uid = element.options[element.selectedIndex].value;
  singleMachineSelectedMachine = uid;
  var coreSelector = document.getElementById('coreSelect');
  coreSelector.innerHTML = '<option></option>';
  var matchingMachine = onlineSlavesBackup.filter(
    function(value, index, array) {
      return value['UID'] == uid;
    }
  );
  if (matchingMachine[0] === undefined) return;
  for (var i = 1; i <= parseInt(matchingMachine[0]['CORES']); i++) {
    var defaultSelected = false;
    var currentSelected = false;
    if (i == 1) defaultSelected = true;
    if (i == matchingMachine[0]['USED_CORES']) currentSelected = true;
    coreSelector.appendChild(
      new Option(i, i, defaultSelected, currentSelected)
    );
  }
  return true;
}

function toggleOnlineClientsDisplay() {
  var checked = document.getElementById('clientsCheckbox').checked;
  clientsCheckboxChecked = checked;
  document.getElementById('offlineClientEvents').innerHTML = '';
  if (!hideOfflineClients) {
    mostRecentHideClientTime = Date.now();
    document.getElementById('clientsCheckbox').checked = false;
    clientsCheckboxChecked = false;
  } else {
    mostRecentHideClientTime = undefined;
    document.getElementById('clientsCheckbox').checked = true;
    clientsCheckboxChecked = true;
  }
  hideOfflineClients = !hideOfflineClients;
  socket.emit('get_info'); // force refresh
}

function toggleOnlineSlavesDisplay() {
  var checked = document.getElementById('slavesCheckbox').checked;
  slavesCheckboxChecked = checked;
  document.getElementById('offlineSlaveEvents').innerHTML = '';
  if (!hideOfflineSlaves) {
    mostRecentHideSlaveTime = Date.now();
    document.getElementById('slavesCheckbox').checked = false;
    slavesCheckboxChecked = false;
  } else {
    mostRecentHideSlaveTime = undefined;
    document.getElementById('slavesCheckbox').checked = true;
    slavesCheckboxChecked = true;
  }
  hideOfflineSlaves = !hideOfflineSlaves;
  socket.emit('get_info'); // force refresh
}

function getPresetFromLocalStorage(configName) {
  if (localStorage.hasOwnProperty('presets')) {
    var presets = JSON.parse(localStorage.getItem('presets'));
    if (presets.hasOwnProperty(configName)) {
      return presets[configName];
    }
  }
  return null;
}

function setPresetInLocalStorage(configName, configData) {
  if (!localStorage.hasOwnProperty('presets')) {
    localStorage['presets'] = JSON.stringify({});
  }
  var presets = JSON.parse(localStorage.getItem('presets'));
  if (presets.hasOwnProperty(configName)) {
    var ok = confirm('You are about to overwrite an existing preset' +
        'configuration. Are you sure you want to continue?');
    if (!ok) return false;
  }
  presets[configName] = configData;
  localStorage.setItem('presets', JSON.stringify(presets));
  return true;
}

function createCpuEditingTable(dataSource) {
  if (dataSource === null) {
    dataSource = onlineSlavesDedupSorted;
    document.getElementById('importConfigMessages').innerHTML =
        'Configuration imported.';
  } else if ('options' in dataSource) {
    dataSource = getPresetFromLocalStorage(
      dataSource.options[dataSource.selectedIndex].value
    );
  }
  var batchForm = document.getElementById('batchExportForm');
  batchForm.innerHTML = '';
  var table = document.createElement('table');
  table.id = 'prepareExportTable';
  var tr = table.insertRow(-1);
  var th = document.createElement('th');      // TABLE HEADER.
  th.innerHTML = 'Hostname';
  tr.appendChild(th);
  th = document.createElement('th');      // TABLE HEADER.
  th.innerHTML = 'Core Count';
  tr.appendChild(th);
  batchForm.appendChild(table);
  for (var i in onlineSlavesDedupSorted) {
    var hostname = onlineSlavesDedupSorted[i]['Hostname'];
    var maxCores = onlineSlavesDedupSorted[i]['CORES'];
    var targetCores = onlineSlavesDedupSorted[i]['USED_CORES'];
    if (dataSource.hasOwnProperty(hostname)) {
      targetCores = dataSource[hostname];
    }
    tr = table.insertRow(-1);
    var tabCell = tr.insertCell(-1);
    tabCell.innerHTML = hostname;
    tabCell = tr.insertCell(-1);
    var thisSelect = document.createElement('select');
    thisSelect.id = 'export-' + hostname;
    exportTableSettings[hostname] = targetCores;
    thisSelect.innerHTML = '<option></option>';
    for (var j = 1; j <= maxCores; j++) {
      var defaultSelected = false;
      var currentSelected = false;
      if (j == targetCores) {
        defaultSelected = true;
        currentSelected = true;
      }
      thisSelect.appendChild(new Option(j, j,
      defaultSelected, currentSelected));
      tabCell.appendChild(thisSelect);
    }
  }
  $("select[id|='export']").change(function() {
    document.getElementById('applyBatchSettingsMessages').innerHTML = '';
    document.getElementById('batchExportMessages').innerHTML = '';
    updateExportTable(this.id, this.value);
  });
  $("select[id|='export']").focus(function() {
    freezeUpdates();
  });
  unfreezeUpdates();
  isConfigurationLoaded = true;
  if (!cpuTableVisible) {
    $('#batchExport').toggle(1);
    cpuTableVisible = true;
  }
}

function displaySavedPresets() {
  if (Date.now() > freezeUpdatesUntil) {
    if (localStorage.hasOwnProperty('presets')) {
      var presets = JSON.parse(localStorage.getItem('presets'));
      var newSelect = document.getElementById('presetsSelect');
      if (newSelect === null) {
        newSelect = document.createElement('select');
        newSelect.id = 'presetsSelect';
        document.getElementById('savedPresets').appendChild(newSelect);
      }
      newSelect.innerHTML = '<option></option>';
      for (var preset in presets) {
        if (preset === selectedPreset) {
          newSelect.appendChild(new Option(preset, preset, true, true));
        } else {
          newSelect.appendChild(new Option(preset, preset, false, false));
        }
      }
      $('#presetsSelect').focus(function() {
        freezeUpdates();
      });
      $('#presetsSelect').change(function() {
        selectedPreset = this.value;
        createCpuEditingTable(this);
      });
    }
  }
}

$(document).ready(function() {
  toggleArrow('batchProcess', 1);
  toggleArrow('singleMachine', 1);
  $('#batchExport').toggle(1);
  function f(e) {
    if (e.shiftKey) {
      if (e.keyCode == 49) { // Shift + 1
        toggleArrow('batchProcess', 300);
      } else if (e.keyCode == 50) { // Shift + 2
        toggleArrow('singleMachine', 300);
      } else if(e.keyCode == 51) { // Shift + 3
        toggleArrow('tables', 300);
      } else if (e.keyCode == 191) { // ? key
        var el = document.getElementById('keyboardShortcutsGuide');
        el.style.visibility =
            (el.style.visibility == 'visible') ? 'hidden' : 'visible';
      }
    }
    else if (e.keyCode == 27) { // Esc key
      hideOverlay();
    }
    else if (e.keyCode == 219)  {// US keyboard [ key
      toggleOnlineClientsDisplay();
    } else if (e.keyCode == 221) {// US keyboard ] key
      toggleOnlineSlavesDisplay();
    }
  }

  $(document).bind('keydown', f);

  var namespace = '/admin';
  socket = io.connect('http://' + document.domain + ':' +
      location.port + namespace);

  socket.on('up_time_resp', function (e) {
    masterUptimeOnConnect = e;
    var newDateObj = new Date(Date.now() - e*1000);
    connStatusChangeTime = newDateObj.getTime();
  });

  socket.on('connect', function() {
    console.log('Connected to Waldorf Master');
    var masterStatusSpan = document.getElementById('masterStatusSpan');
    masterStatusSpan.innerText = 'CONNECTED';
    masterStatusSpan.style = 'font-size: 16px; color:#222;' +
        'background-color:#9dd05e; padding: 5px 5px 0px 5px;';
    document.getElementById('masterStatusTimer').style = 'font-size: 16px;';
    connStatusChangeTime = Date.now()
    socket.emit('up_time');
  });

  socket.on('check_ver_resp', function (e) {
    masterVersion = e;
    masterVersionSpan.innerHTML = 'v' + e;
  });

  socket.on('disconnect', function() {
    console.log('Disconnected from Master');
    masterVersion = undefined;
    var masterStatusSpan = document.getElementById('masterStatusSpan');
    masterStatusSpan.innerText = 'CONNECTION LOST';
    masterStatusSpan.style = 'font-size: 16px; background-color:#F58686; ' +
        'color:#222; padding: 5px 5px 0px 5px;';
    document.getElementById('masterStatusTimer').style = 'font-size: 16px;';
    connStatusChangeTime = Date.now()
  });

  socket.on('reconnect', function() {
    console.log('Reconnected with Master');
  });

  var fileInput = document.getElementById('fileInput');
  var fileDisplayArea = document.getElementById('fileDisplayArea');
  fileInput.addEventListener('change', function(e) {
    var file = fileInput.files[0];
    var textType = 'application/json';

    if (file.type == textType || file.type == '') {
      var reader = new FileReader();
      reader.onload = function(e) {
        var data = JSON.parse(reader.result);
        if (!(data.hasOwnProperty('machines') &&
              data.hasOwnProperty('config_name')))
        {
          fileDisplayArea.innerText =
              'Invalid configuration file. Please try a different file.';
          return false;
        }
        fileDisplayArea.innerText = '';
        setPresetInLocalStorage(data.config_name, data.machines);
        createCpuEditingTable(data.machines);
      }
      reader.readAsText(file);
    } else {
      fileDisplayArea.innerText = 'File not supported!';
    }
  });

  socket.on('get_info_resp', function(msg) {
    displaySavedPresets();
    var msgObjectsClone = JSON.parse(JSON.stringify(msg.objects));
    var msgObjectsKeys = Object.keys(msgObjectsClone);
    var keysSorted = msgObjectsKeys.sort(function(a,b) {
      if (msgObjectsClone[a]['Hostname'] != msgObjectsClone[b]['Hostname']) {
        // alphabetical order, ascending
        return msgObjectsClone[a]['Hostname'] > msgObjectsClone[b]['Hostname'];
      } else {
        // most recent at the bottom
        return Date.parse(msgObjectsClone[a]['ConnectTime']) >
               Date.parse(msgObjectsClone[b]['ConnectTime']);
      }
    });

    var allMachines = [];
    for (var k in keysSorted) {
      allMachines.push(msgObjectsClone[keysSorted[k]]);
    }

    allSlavesBackup = allMachines.filter(function(value, index, array) {
      return value['Type'] == 'Slave';
    });

    var onlineSlaves = allSlavesBackup.filter(function(value, index, array) {
      return value['State'] == 'Online';
    });

    onlineSlavesBackup = JSON.parse(JSON.stringify(onlineSlaves));

    var alreadySeen = [];
    var onlineSlavesCopyForSort = JSON.parse(JSON.stringify(onlineSlaves));
    onlineSlavesCopyForSort.sort(function(a,b) {
      return a['USED_CORES'] < b['USED_CORES']; // sort in reverse order
    });
    var onlineSlavesFiltered = [];
    for (var i in onlineSlavesCopyForSort) {
      if (!alreadySeen.includes(onlineSlavesCopyForSort[i]['Hostname'])) {
        alreadySeen.push(onlineSlavesCopyForSort[i]['Hostname']);
        onlineSlavesFiltered.push(onlineSlavesCopyForSort[i]);
      }
    }

    onlineSlavesDedupSorted = JSON.parse(JSON.stringify(onlineSlavesFiltered));
    var fileDisplayArea = document.getElementById('fileDisplayArea');
    if (onlineSlavesDedupSorted.length == 0) {
      fileDisplayArea.innerHTML =
          'No slaves connected. Please add machines to the ' +
          'Waldorf network first.';
      if (singleMachineFormVisible) {
        singleMachineFormVisible = false;
        $('#singleMachineForm').toggle(1);
        document.getElementById('singleMachineNotice').innerHTML =
            'There are no slaves connected';
        $('#importConfig').toggle(1);
        document.getElementById('fileDisplayArea').innerHTML =
            'There are no slaves connected';
      }
      if (cpuTableVisible) {
        cpuTableVisible = false;
        $('#batchExport').toggle(1);
      }
    } else {
      if (!singleMachineFormVisible) {
        singleMachineFormVisible = true;
        $('#singleMachineForm').toggle(1);
        document.getElementById('singleMachineNotice').innerHTML = '';
        $('#importConfig').toggle(1);
        document.getElementById('fileDisplayArea').innerHTML = '';
      }
      if (!cpuTableVisible) {
        if (isConfigurationLoaded) {
          $('#batchExport').toggle(1);
          cpuTableVisible = true;
        }
      }
    }

    var clients = allMachines.filter(function(value, index, array) {
      if (hideOfflineClients) {
        return value['Type'] == 'Client' && value['State'] == 'Online';
      } else {
        return value['Type'] == 'Client';
      }
    });

    var clientsOffline = allMachines.filter(function(value, index, array) {
      return value['Type'] == 'Client' && value['State'] != 'Online';
    });

    var newClientOfflineEvents = clientsOffline.filter(
      function(value, index, array) {
        if (mostRecentHideClientTime !== undefined) {
          return Date.parse(value['DisconnectTime']) > mostRecentHideClientTime;
        }
        return false;
      }
    );

    newClientOfflineEvents.sort(function(a,b) {
      return a['DisconnectTime'] > b['DisconnectTime'];
    });

    if (newClientOfflineEvents.length > 0) {
      var clientEventsDiv = document.getElementById('offlineClientEvents');
      clientEventsDiv.innerHTML = 'New offline events (' +
          newClientOfflineEvents.length + '):<br /><br />';
      for (var h in newClientOfflineEvents) {
        clientEventsDiv.innerHTML += newClientOfflineEvents[h]['Hostname'] +
            ' ('+ newClientOfflineEvents[h]['DisconnectTime'] + ')' + '<br />';
      }
      clientEventsDiv.innerHTML += '<br />Click to hide this information.' +
          '<br /><br />';
    }

    var slavesOffline = allMachines.filter(function(value, index, array) {
      return value['Type'] == 'Slave' && value['State'] != 'Online';
    });
    var newSlaveOfflineEvents = slavesOffline.filter(
      function(value, index, array) {
        if (mostRecentHideSlaveTime !== undefined) {
          return Date.parse(value['DisconnectTime']) > mostRecentHideSlaveTime;
        }
        return false;
      }
    );
    newSlaveOfflineEvents.sort(function(a,b) {
      return a['DisconnectTime'] > b['DisconnectTime'];
    });

    if (newSlaveOfflineEvents.length > 0) {
      var slaveEventsDiv = document.getElementById('offlineSlaveEvents');
      slaveEventsDiv.innerHTML = 'New offline events (' +
          newSlaveOfflineEvents.length + '):<br /><br />';
      for (var h in newSlaveOfflineEvents) {
        slaveEventsDiv.innerHTML += newSlaveOfflineEvents[h]['Hostname'] +
            ' ('+ newSlaveOfflineEvents[h]['DisconnectTime'] + ')' + '<br />';
      }
      slaveEventsDiv.innerHTML += '<br />Click to hide this information.' +
          '<br /><br />';
    }
    var slaves = allMachines.filter(function(value, index, array) {
      if (hideOfflineSlaves) {
        return value['Type'] == 'Slave' && value['State'] == 'Online';
      } else {
        return value['Type'] == 'Slave';
      }
    });

    var totalCoreCount = onlineSlavesFiltered.reduce(function(a, b) {
      return parseInt(a) + parseInt(b['CORES']);
    }, 0);

    var onlineClients = clients.filter(function(value, index, array) {
      return value['State'] == 'Online';
    });

    var usedCoreCount = onlineSlavesFiltered.reduce(function(a, b) {
      return parseInt(a) + parseInt(b['USED_CORES']);
    }, 0);

    var cpuUsageRate = 0;
    if (totalCoreCount > 0) {
      cpuUsageRate = Math.floor(100.0 * usedCoreCount / totalCoreCount);
    }
    document.getElementById('slaveCounter').innerHTML = '(' +
        onlineSlavesBackup.length + '): Using ' + usedCoreCount + '/' +
        totalCoreCount + ' cores (' + cpuUsageRate +'%) on ' +
        onlineSlavesFiltered.length + ' host';
    if (onlineSlavesFiltered.length != 1) {
      document.getElementById('slaveCounter').innerHTML += 's';
    }
    document.getElementById('clientCounter').innerText = '(' +
        onlineClients.length + ')';

    if (clients.length > 0) {
      createTableFromJson(msg.head, clients, 'clients');
    } else {
      document.getElementById('clientsDiv').innerHTML =
          'There are no clients currently connected';
    }
    if (slaves.length > 0) {
      createTableFromJson(msg.head, slaves, 'slaves');
    } else {
      document.getElementById('slavesDiv').innerHTML =
          'There are no slaves connected';
    }
  });

  socket.on('change_core_resp', function(msg) {
    console.log('Core change', msg);
  });

  var refreshInfo = function() {
    if (masterVersion === undefined) {
      socket.emit('check_ver');
    }
    socket.emit('get_info');
    return false;
  }

  setInterval(refreshInfo, 3000);
  setInterval(connTimerUpdate, 1000);

  function createTableFromJson(headers, msgData, tableDivName) {
    if (Date.now() > freezeUpdatesUntil) {
      if (tableDivName == 'slaves') {
        var selectObj = document.getElementById('singleSelect');
        selectObj.innerHTML = '<option></option>';
        var machines = []
        var uids = []
        for (var machine in onlineSlavesBackup) {
          machines.push(onlineSlavesBackup[machine]['Hostname']);
          uids.push(onlineSlavesBackup[machine]['UID']);
        }
        for (var i in machines) {
          if (uids[i] == singleMachineSelectedMachine) {
            selectObj.appendChild(new Option(machines[i] + ' (' +
                uids[i] + ')', uids[i], true, true));
          } else {
            selectObj.appendChild(new Option(machines[i] + ' (' +
                uids[i] + ')', uids[i], false, false));
          }
        }
      }
    }
    // Extract values for table header
    var col = [];
    for (var h in headers) {
      col.push(h);
    }
    // Create table dynamically
    var table = document.createElement('table');
    table.id = tableDivName + 'Table';
    // Create HTML table headers
    var tr = table.insertRow(-1); // table row
    for (var i = 0; i < col.length; i++) {
      var th = document.createElement('th'); // table header
      th.innerHTML = headers[col[i]];
      tr.appendChild(th);
    }
    // Add data from Master to the table
    for (var key in msgData) {
      var tr = table.insertRow(-1);
      var activeCoreCount = 0;
      var currentUid = '';
      for (var key2 in msgData[key]) {
        var tabCell = tr.insertCell(-1);
        if (key2 === 'State') {
          tabCell.style='color:#222;background-color:#9dd05e;';
          if (msgData[key][key2] != 'Online') {
            tabCell.style='color:#222;background-color:#F58686;';
          }
        }
        if (key2 == 'Version' && masterVersion != undefined) {
          tabCell.style='color:#222;background-color:#9dd05e;';
          if (msgData[key][key2] != masterVersion) {
            tabCell.style='color:#222;background-color:#F58686;';
          }
        }
        if (key2 === 'UID') {
          currentUid = msgData[key][key2];
        }
        if (key2 === 'CORES') {
          activeCoreCount = msgData[key][key2];
          tabCell.innerHTML = '';
          for (var k = 0; k < msgData[key]['USED_CORES']; k++) {
            tabCell.innerHTML += (1+k)%10 + ' ';
          }
          for (var k = msgData[key]['USED_CORES'];
               k < msgData[key][key2]; k++)
          {
            tabCell.innerHTML += '- ';
          }
        } else {
          tabCell.innerHTML = msgData[key][key2];
        }
      }
    }
    // Append newly created table to HTML div
    var divContainer = document.getElementById(tableDivName + 'Div');
    var headRowClassName = []
    if (tableDivName=='slaves' && divContainer.childNodes.length > 0) {
      var existingTable = divContainer.childNodes[3];
      if (existingTable.tHead !== undefined) {
        headrow = existingTable.tHead.rows[0].cells;
        for (var i = 0; i < headrow.length; i++) {
          if (headrow[i].className.search(/sorttable_sorted_reverse/) != -1) {
            headRowClassName = [i, headrow[i].className, 0];
          } else if (headrow[i].className.search(/sorttable_sorted/) != -1) {
            headRowClassName = [i, headrow[i].className, 1];
          }
        }
      }
    }

    if (tableDivName != 'slaves') {
      var clientHTML = 'Show offline clients (press \'[\' to toggle): ' +
          '<input type="checkbox" id="clientsCheckbox" ' +
          'onclick="toggleOnlineClientsDisplay()" ';
      if (clientsCheckboxChecked) {
        clientHTML += ' checked="checked"'
      }
      clientHTML += '/>';
      divContainer.innerHTML = clientHTML;
      divContainer.appendChild(table);
      return false;
    }

    sorttable.makeSortable(table);
    table.className = 'sortable';
    if (headRowClassName.length > 0) {
      headrow = table.tHead.rows[0].cells;
      for (var i = 0; i < headrow.length; i++) {
        if (i == headRowClassName[0]) {
          headrow[i].id = tableDivName + 'Marked';
        } else {
          delete headrow[i].id;
          delete headrow[i].className;
        }
      }
    }

    var slavesHTML = 'Show offline slaves (press \']\' to toggle): ' +
        '<input type="checkbox" id="slavesCheckbox" ' +
        'onclick="toggleOnlineSlavesDisplay()" ';
    if (slavesCheckboxChecked) {
      slavesHTML += 'checked="checked"'
    }
    slavesHTML +='/><p>The slaves table can be sorted by choosing ' +
        'a column header</p>';
    divContainer.innerHTML = slavesHTML;
    divContainer.appendChild(table);

    if (headRowClassName.length > 0) {
      sorttable.innerSortFunction.apply(
          document.getElementById(tableDivName + 'Marked'), []
      );
      if (headRowClassName[2] == 0) {
        var temp = document.getElementById(tableDivName + 'Table').tBodies[0];
        sorttable.reverse(temp);
        document.getElementById(tableDivName + 'Table').tBodies[0] = temp;
        that = document.getElementById(tableDivName + 'Marked');
        that.className = that.className.replace('sorttable_sorted',
                                                'sorttable_sorted_reverse');
        that.removeChild(document.getElementById('sorttable_sortfwdind'));
        sortrevind = document.createElement('span');
        sortrevind.id = 'sorttable_sortrevind';
        sortrevind.innerHTML = stIsIE ?
            '&nbsp<font face="webdings">5</font>' : '&nbsp;&#x25B4;';
        that.appendChild(sortrevind);
      }
    }
  }

  // first time load
  refreshInfo();
});
